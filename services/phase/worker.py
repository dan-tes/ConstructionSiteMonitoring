"""Phase service — current project phase (диаграмма: блок 3).

Consumes `phase.command` from the shared `csm.analysis` exchange, computes a
result, publishes it on `phase.result`. See backend/integrations/README.md
for the pipeline topology this is one leg of.

REAL model: the trained ConstructionPhaseModel (model.py, weights/best.pt —
same checkpoint as phase_determination/data/construction_phase_checkpoints,
see that notebook's "## 11. Load best model and evaluate" for where it came
from), fed a real `WINDOW_SIZE`-day (128) sequence of daily equipment
observations, matching how it was trained — see
phase_determination/construction_phase_training.ipynb's
`build_project_timeline()` (one row per calendar day, `WINDOW_SIZE`
consecutive days per training window).

`command.history` is the project's actual day-by-day equipment counts (see
backend's `EquipmentObservation`/`analysis.py`), but it's necessarily
*sparse* — a project doesn't get filmed every day — and covers however much
of the project's life is on record, not a fixed 128 days. `_build_window()`
below turns that into the dense daily sequence the model expects: for each
of the `WINDOW_SIZE` calendar days ending on `command.as_of_date`, it uses
that day's real counts if there's an entry for it, otherwise forward-fills
the most recent earlier day's counts (equipment on an active site doesn't
reset to zero just because nobody filmed it that day — treating a gap as
"no equipment" would itself be a fabricated, out-of-distribution signal),
and falls back to all-zero counts only for days before the project's first
ever recorded observation (indistinguishable from — and encoded exactly
like — a legitimately equipment-free day, which the training data has
plenty of during e.g. Preconstruction).

This replaces an earlier version that fed the model a single-timestep
observation (just this one video's counts, no history) — confirmed by
direct testing to produce systematically wrong predictions for equipment
whose phase association isn't overwhelmingly one-sided (a project's
`bulldozer` count alone was classified as `"Preconstruction"` with 79%
confidence, despite `bulldozer` never once appearing in a Preconstruction
activity in the training data — see git history for the writeup). A
length-1 sequence is essentially out of the training distribution (training
windows are `WINDOW_SIZE` real calendar days, with gaps never modelled at
all), which is a materially worse problem than only *missing historical
context* the earlier docstring here described.

Still a known simplification versus training: this always runs the
noise-free case for the two detector-confidence features (`visible`,
`confidence` both 1.0 on every day with data) rather than the miss/
false-positive-perturbed values the training data simulated — a reasonable
stand-in, not a faithful reproduction of training-time inference.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import aio_pika
import numpy as np
import pandas as pd
import torch
from sentence_transformers import SentenceTransformer

from model import ConstructionPhaseModel
from schemas import DailyEquipmentCounts, Envelope, PhaseCommand, PhaseResult

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("csm.phase")

RABBITMQ_URL = os.environ.get("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")
EXCHANGE_NAME = "csm.analysis"  # must match backend/integrations/broker.py
COMMAND_ROUTING_KEY = "phase.command"
RESULT_ROUTING_KEY = "phase.result"
COMMAND_QUEUE = "phase.command.q"

DATA_DIR = Path(__file__).parent / "data"
CHECKPOINT_PATH = Path(__file__).parent / "weights" / "best.pt"
TEXT_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"


def _build_phase_meta() -> tuple[list[str], list[str], dict[str, np.ndarray], pd.DataFrame]:
    """Reproduces retrain_weighted_equipment.py's feature construction
    exactly — same groupby, same phase_text template, same per-phase
    equipment *prevalence* — so the sentence-transformer embeddings and the
    structured features match what the checkpoint was trained on.

    `phase_equipment` used to be a binary set (any activity of that phase
    ever using class X counted the same as every activity using it) —
    confirmed by direct testing to make the model over-predict MEP for
    equipment mixes that only weakly resemble it (tower_crane/hanging_hook
    appear in just 21% of MEP's activities but 91% of Structural Frame's,
    yet both were fed in as an identical "yes, expected"). It's now each
    class's actual prevalence fraction within that phase's activities — see
    phase_determination/retrain_weighted_equipment.py, which retrained
    `best.pt` against this same feature and confirmed the fix (test
    accuracy 89%, MEP recall dropping as it stopped being the over-eager
    default) on held-out projects, not just the one case that found it."""
    activities = pd.read_csv(DATA_DIR / "activities_with_equipment.csv")
    equipment_desc = pd.read_csv(DATA_DIR / "equipment_descriptions.csv")
    activities["expected_equipment"] = activities["expected_equipment"].apply(
        lambda x: eval(x) if isinstance(x, str) else x
    )

    equipment_classes = equipment_desc["equipment_class"].tolist()
    equipment_to_id = {e: i for i, e in enumerate(equipment_classes)}
    num_equipment = len(equipment_classes)
    equipment_descriptions = dict(
        zip(equipment_desc["equipment_class"], equipment_desc["description"])
    )

    phase_meta = (
        activities.groupby(["phase", "phase_order"], as_index=False)
        .agg(
            activities=("activity_name", lambda x: list(dict.fromkeys(x))),
            duration_days=("planned_duration_days", "sum"),
            criticality=("criticality", "mean"),
        )
        .sort_values("phase_order")
        .reset_index(drop=True)
    )
    phase_names = phase_meta["phase"].tolist()

    phase_equipment_prevalence: dict[str, np.ndarray] = {}
    phase_texts = []
    for _, row in phase_meta.iterrows():
        phase = row["phase"]
        phase_activities = activities.loc[activities["phase"] == phase, "expected_equipment"]
        n_activities = len(phase_activities)
        counts = np.zeros(num_equipment, dtype=np.float32)
        for eqs in phase_activities:
            for e in eqs:
                if e in equipment_to_id:
                    counts[equipment_to_id[e]] += 1
        prevalence = counts / max(n_activities, 1)
        phase_equipment_prevalence[phase] = prevalence

        present = [(equipment_classes[i], prevalence[i]) for i in range(num_equipment) if prevalence[i] > 0]
        present.sort(key=lambda t: -t[1])
        equipment_text = "\n".join(
            f"- {e}: present in {p:.0%} of this phase's activities. {equipment_descriptions[e]}"
            for e, p in present
        )
        activities_text = ", ".join(row["activities"])
        phase_texts.append(
            f"Construction phase: {phase}. Activities: {activities_text}. "
            f"Expected equipment with prevalence: {', '.join(f'{e} ({p:.0%})' for e, p in present)}.\n"
            f"Equipment descriptions:\n{equipment_text}"
        )
    phase_meta["phase_text"] = phase_texts

    return phase_names, equipment_classes, phase_equipment_prevalence, phase_meta


def _build_phase_structured(
    phase_names: list[str], equipment_classes: list[str], phase_equipment_prevalence, phase_meta
) -> torch.Tensor:
    duration = phase_meta["duration_days"].to_numpy(dtype=np.float32)
    duration = duration / max(duration.max(), 1.0)
    criticality = phase_meta["criticality"].to_numpy(dtype=np.float32)

    rows = []
    for i, phase in enumerate(phase_names):
        rows.append(
            np.concatenate(
                [phase_equipment_prevalence[phase], np.array([duration[i], criticality[i]], dtype=np.float32)]
            )
        )
    return torch.tensor(np.stack(rows), dtype=torch.float32)


log.info("building phase metadata from %s", DATA_DIR)
PHASE_NAMES, EQUIPMENT_CLASSES, _phase_equipment, _phase_meta = _build_phase_meta()
NUM_PHASES = len(PHASE_NAMES)
NUM_EQUIPMENT = len(EQUIPMENT_CLASSES)

log.info("loading checkpoint from %s", CHECKPOINT_PATH)
_checkpoint = torch.load(CHECKPOINT_PATH, map_location="cpu")
if _checkpoint["phase_names"] != PHASE_NAMES or _checkpoint["equipment_classes"] != EQUIPMENT_CLASSES:
    raise RuntimeError(
        "bundled data/*.csv does not match the phase_names/equipment_classes "
        "the checkpoint was trained with — data/checkpoint are out of sync"
    )

log.info("encoding phase text with %s", TEXT_MODEL_NAME)
_text_encoder = SentenceTransformer(TEXT_MODEL_NAME, device="cpu")
with torch.no_grad():
    PHASE_TEXT_EMBEDDINGS = (
        _text_encoder.encode(
            _phase_meta["phase_text"].tolist(), convert_to_tensor=True, normalize_embeddings=True
        )
        .float()
        .unsqueeze(0)
    )  # (1, NUM_PHASES, 384)

PHASE_STRUCTURED = _build_phase_structured(
    PHASE_NAMES, EQUIPMENT_CLASSES, _phase_equipment, _phase_meta
).unsqueeze(0)  # (1, NUM_PHASES, NUM_EQUIPMENT + 2)

_config = _checkpoint["config"]
WINDOW_SIZE = _config["window_size"]
_model = ConstructionPhaseModel(
    observation_dim=_config["observation_dim"],
    phase_text_dim=_config["phase_text_dim"],
    phase_structured_dim=_config["phase_structured_dim"],
    max_len=WINDOW_SIZE,
)
_model.load_state_dict(_checkpoint["model_state_dict"])
_model.eval()
log.info("phase model ready (%d params)", sum(p.numel() for p in _model.parameters()))


def _daily_observation(counts_by_class: dict[str, int]) -> np.ndarray:
    counts = np.zeros(NUM_EQUIPMENT, dtype=np.float32)
    for cls, n in counts_by_class.items():
        if cls in EQUIPMENT_CLASSES:
            counts[EQUIPMENT_CLASSES.index(cls)] = float(n)
    count_feature = np.clip(counts, 0, 5) / 5.0
    visible = np.ones(NUM_EQUIPMENT, dtype=np.float32)
    confidence = np.ones(NUM_EQUIPMENT, dtype=np.float32)
    return np.concatenate([count_feature, visible, confidence]).astype(np.float32)


def _build_window(history: list[DailyEquipmentCounts], as_of_date: date) -> np.ndarray:
    """One row per calendar day for the `WINDOW_SIZE` days ending on
    `as_of_date` — see module docstring for the forward-fill/zero-fill
    rules. `history` is sorted here since callers (and tests) shouldn't have
    to guarantee ordering themselves."""
    history = sorted(history, key=lambda h: h.date)
    window_start = as_of_date - timedelta(days=WINDOW_SIZE - 1)

    rows = []
    last_counts: dict[str, int] = {}
    h_idx = 0
    day = window_start
    while day <= as_of_date:
        while h_idx < len(history) and history[h_idx].date <= day:
            last_counts = {c.equipment_class: c.count for c in history[h_idx].counts}
            h_idx += 1
        rows.append(_daily_observation(last_counts))
        day += timedelta(days=1)
    return np.stack(rows)  # (WINDOW_SIZE, observation_dim)


def compute(command: PhaseCommand) -> PhaseResult:  # EXTENSION POINT
    window = _build_window(command.history, command.as_of_date)
    observations = torch.from_numpy(window).unsqueeze(0)  # (1, WINDOW_SIZE, observation_dim)

    latest_counts = window[-1, :NUM_EQUIPMENT] * 5.0  # undo the /5 clip-normalization for logging
    seen = ", ".join(
        f"{cls}: {int(round(n))}" for cls, n in zip(EQUIPMENT_CLASSES, latest_counts) if n > 0
    ) or "none"
    log.info(
        "classifying phase as of %s from a %d-day window (%d days with real data) — latest day: %s",
        command.as_of_date,
        len(window),
        len(command.history),
        seen,
    )

    with torch.no_grad():
        output = _model(
            observations=observations,
            phase_text_embeddings=PHASE_TEXT_EMBEDDINGS,
            phase_structured=PHASE_STRUCTURED,
        )
    probs = torch.softmax(output.emissions[0, -1], dim=-1)  # last position == as_of_date
    idx = int(torch.argmax(probs).item())
    phase_name = PHASE_NAMES[idx]

    top3 = torch.topk(probs, min(3, NUM_PHASES))
    ranked = ", ".join(
        f"{PHASE_NAMES[i]} {p:.0%}" for p, i in zip(top3.values.tolist(), top3.indices.tolist())
    )
    matched_stage_index = next(
        (i for i, s in enumerate(command.plan_stages) if s.phase.strip().lower() == phase_name.lower()),
        None,
    )
    log.info(
        "found phase %r (confidence %.0f%%) — top-3: %s; matched plan stage index: %s",
        phase_name,
        float(probs[idx]) * 100,
        ranked,
        matched_stage_index,
    )

    return PhaseResult(
        status="done",
        phase_name=phase_name,
        confidence=float(probs[idx]),
        matched_stage_index=matched_stage_index,
    )


async def main() -> None:
    connection = await aio_pika.connect_robust(RABBITMQ_URL)
    async with connection:
        channel = await connection.channel()
        await channel.set_qos(prefetch_count=1)
        exchange = await channel.declare_exchange(
            EXCHANGE_NAME, aio_pika.ExchangeType.TOPIC, durable=True
        )
        queue = await channel.declare_queue(COMMAND_QUEUE, durable=True)
        await queue.bind(exchange, COMMAND_ROUTING_KEY)

        async def on_message(message: aio_pika.abc.AbstractIncomingMessage) -> None:
            async with message.process():
                envelope = Envelope[PhaseCommand].model_validate_json(message.body)
                try:
                    result = await asyncio.to_thread(compute, envelope.payload)
                except Exception as exc:  # noqa: BLE001 - reported back as a failed result
                    log.exception("compute failed for asset %s", envelope.correlation_id)
                    result = PhaseResult(status="failed", error=str(exc))

                reply = Envelope(
                    correlation_id=envelope.correlation_id,
                    published_at=datetime.now(timezone.utc),
                    payload=result,
                )
                await exchange.publish(
                    aio_pika.Message(
                        body=reply.model_dump_json().encode(),
                        delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                    ),
                    routing_key=RESULT_ROUTING_KEY,
                )

        await queue.consume(on_message)
        log.info("phase worker consuming %s from %s", COMMAND_ROUTING_KEY, COMMAND_QUEUE)
        await asyncio.Future()  # run forever


if __name__ == "__main__":
    asyncio.run(main())
