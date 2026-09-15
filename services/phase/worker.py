"""Phase service — current project phase (диаграмма: блок 3).

Consumes `phase.command` from the shared `csm.analysis` exchange, computes a
result, publishes it on `phase.result`. See backend/integrations/README.md
for the pipeline topology this is one leg of.

REAL model: the trained ConstructionPhaseModel (model.py, weights/best.pt —
same checkpoint as phase_determination/data/construction_phase_checkpoints,
see that notebook's "## 11. Load best model and evaluate" for where it came
from), fed a *single-timestep* observation built from `command.events`.

Known simplification: the model was trained on 128-day windows of daily
equipment observations (one row per day, built by replaying a project's full
event history — see build_project_timeline() in the training notebook). This
service only ever gets the events attached to *this* video (a handful of
recent arrivals, no multi-day history), so it feeds the model a sequence of
length 1 instead — the positional encoding and transformer both tolerate a
shorter sequence just fine, but the model never sees "how the equipment mix
changed over the past N days", only "what's visible right now". It also runs
the noise-free case for the two detector-confidence features (`visible`,
`confidence` both 1.0) rather than the miss/false-positive-perturbed values
the training data simulated. Both are reasonable stand-ins for a live single
video, not a faithful reproduction of training-time inference — a real
multi-day observation history (module 2 aggregating detections into daily
occupancy, kept per project) is the natural next step; see
backend/integrations/README.md Open items.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import aio_pika
import numpy as np
import pandas as pd
import torch
from sentence_transformers import SentenceTransformer

from model import ConstructionPhaseModel
from schemas import Envelope, PhaseCommand, PhaseResult

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


def _build_phase_meta() -> tuple[list[str], list[str], dict[str, list[str]], pd.DataFrame]:
    """Reproduces training notebook cells 7 & 9 exactly — same groupby, same
    phase_text template — so the sentence-transformer embeddings and the
    structured features match what the checkpoint was trained on."""
    activities = pd.read_csv(DATA_DIR / "activities_with_equipment.csv")
    equipment_desc = pd.read_csv(DATA_DIR / "equipment_descriptions.csv")
    activities["expected_equipment"] = activities["expected_equipment"].apply(
        lambda x: eval(x) if isinstance(x, str) else x
    )

    equipment_classes = equipment_desc["equipment_class"].tolist()
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

    phase_equipment: dict[str, list[str]] = {}
    phase_texts = []
    for _, row in phase_meta.iterrows():
        phase = row["phase"]
        phase_activities = activities.loc[activities["phase"] == phase, "expected_equipment"]
        eq_set: set[str] = set()
        for eqs in phase_activities:
            eq_set.update(eqs)
        eqs = [e for e in equipment_classes if e in eq_set]
        phase_equipment[phase] = eqs
        equipment_text = "\n".join(f"- {e}: {equipment_descriptions[e]}" for e in eqs)
        activities_text = ", ".join(row["activities"])
        phase_texts.append(
            f"Construction phase: {phase}. Activities: {activities_text}. "
            f"Expected equipment: {', '.join(eqs)}.\nEquipment descriptions:\n{equipment_text}"
        )
    phase_meta["phase_text"] = phase_texts

    return phase_names, equipment_classes, phase_equipment, phase_meta


def _build_phase_structured(
    phase_names: list[str], equipment_classes: list[str], phase_equipment, phase_meta
) -> torch.Tensor:
    num_equipment = len(equipment_classes)
    duration = phase_meta["duration_days"].to_numpy(dtype=np.float32)
    duration = duration / max(duration.max(), 1.0)
    criticality = phase_meta["criticality"].to_numpy(dtype=np.float32)

    rows = []
    for i, phase in enumerate(phase_names):
        eq = np.zeros(num_equipment, dtype=np.float32)
        for e in phase_equipment[phase]:
            eq[equipment_classes.index(e)] = 1.0
        rows.append(np.concatenate([eq, np.array([duration[i], criticality[i]], dtype=np.float32)]))
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
_model = ConstructionPhaseModel(
    observation_dim=_config["observation_dim"],
    phase_text_dim=_config["phase_text_dim"],
    phase_structured_dim=_config["phase_structured_dim"],
    max_len=_config["window_size"],
)
_model.load_state_dict(_checkpoint["model_state_dict"])
_model.eval()
log.info("phase model ready (%d params)", sum(p.numel() for p in _model.parameters()))


def compute(command: PhaseCommand) -> PhaseResult:  # EXTENSION POINT
    counts = np.zeros(NUM_EQUIPMENT, dtype=np.float32)
    for event in command.events:
        if event.event == "arrival" and event.equipment_class in EQUIPMENT_CLASSES:
            counts[EQUIPMENT_CLASSES.index(event.equipment_class)] += 1.0

    seen = ", ".join(
        f"{cls}: {int(n)}" for cls, n in zip(EQUIPMENT_CLASSES, counts) if n > 0
    ) or "none"
    log.info("classifying phase from %d equipment events — seen: %s", len(command.events), seen)

    count_feature = np.clip(counts, 0, 5) / 5.0
    visible = np.ones(NUM_EQUIPMENT, dtype=np.float32)
    confidence = np.ones(NUM_EQUIPMENT, dtype=np.float32)
    obs = np.concatenate([count_feature, visible, confidence]).astype(np.float32)
    observations = torch.from_numpy(obs).unsqueeze(0).unsqueeze(0)  # (1, 1, observation_dim)

    with torch.no_grad():
        output = _model(
            observations=observations,
            phase_text_embeddings=PHASE_TEXT_EMBEDDINGS,
            phase_structured=PHASE_STRUCTURED,
        )
    probs = torch.softmax(output.emissions[0, 0], dim=-1)
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
