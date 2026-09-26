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

Features and the dense window are built by features.py — the same code
the training script (phase_determination/train_phase_v2.py) uses, so the
window the model sees here matches training. The checkpoint's
`config.feature_version` picks the layout: v1 is the original notebook
checkpoint (which had a train/serve mismatch — its training counts were
ground truth, not detector output, and prod fed `visible`/`confidence` as
constant 1.0), v2 is trained on simulated detector output and additionally
knows which days had a real photo vs forward-filled ones — see features.py.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import date, datetime, timezone
from pathlib import Path

import aio_pika
import numpy as np
import torch
from sentence_transformers import SentenceTransformer

from features import FEATURE_VERSION_LEGACY, build_phase_meta, build_phase_structured, build_window
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
WEIGHTS_DIR = Path(__file__).parent / "weights"
# Ансамбль моделей по технике: "путь=вес,путь=вес" (пути относительно
# weights/). По умолчанию — v3 (best.pt: v2, дообученная на 60 размеченных
# таймлапс-стройках, phase_determination/finetune_timelapse.py) и v2 без
# таймлапсов (best_base.pt), веса 3:1 — вместе с visual_phase это лучший
# вариант held-out оценки phase_determination/eval_all.py (~66% по дням).
# Вероятности объединяются log-линейно: log p = sum w_i log p_i.
# PHASE_CHECKPOINT=<путь> — одна модель, для бенчмарка
# (phase_determination/timelapse_phase_eval.py) тем же прод-кодом.
if os.environ.get("PHASE_CHECKPOINT"):
    CHECKPOINTS = [(Path(os.environ["PHASE_CHECKPOINT"]), 1.0)]
else:
    CHECKPOINTS = [
        (WEIGHTS_DIR / spec.split("=")[0].strip(), float(spec.split("=")[1]) if "=" in spec else 1.0)
        for spec in os.environ.get("PHASE_CHECKPOINTS", "best.pt=3,best_base.pt=1").split(",")
    ]
TEXT_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"


log.info("building phase metadata from %s", DATA_DIR)
PHASE_NAMES, EQUIPMENT_CLASSES, _phase_equipment, _phase_meta = build_phase_meta(DATA_DIR)
NUM_PHASES = len(PHASE_NAMES)
NUM_EQUIPMENT = len(EQUIPMENT_CLASSES)

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

PHASE_STRUCTURED = torch.from_numpy(
    build_phase_structured(PHASE_NAMES, _phase_equipment, _phase_meta)
).unsqueeze(0)  # (1, NUM_PHASES, NUM_EQUIPMENT + 2)

def _load_member(path: Path, weight: float) -> dict:
    checkpoint = torch.load(path, map_location="cpu")
    if checkpoint["phase_names"] != PHASE_NAMES or checkpoint["equipment_classes"] != EQUIPMENT_CLASSES:
        raise RuntimeError(
            f"bundled data/*.csv does not match the phase_names/equipment_classes {path.name} "
            "was trained with — data/checkpoint are out of sync"
        )
    config = checkpoint["config"]
    model = ConstructionPhaseModel(
        observation_dim=config["observation_dim"],
        phase_text_dim=config["phase_text_dim"],
        phase_structured_dim=config["phase_structured_dim"],
        max_len=config["window_size"],
        causal=config.get("causal", False),
    )
    model.load_state_dict(checkpoint["model_state_dict"])
    model.eval()
    # чекпоинты до features.py версии не хранили — это v1
    version = config.get("feature_version", FEATURE_VERSION_LEGACY)
    log.info("loaded %s (weight %g, features v%d, window %d)", path.name, weight, version, config["window_size"])
    return {"name": path.name, "model": model, "weight": weight, "window": config["window_size"], "version": version}


# Недостающий участник ансамбля (напр. best_base.pt не попал в деплой —
# *.pt в .gitignore) не роняет сервис: работаем на оставшихся моделях.
for _path, _ in CHECKPOINTS:
    if not _path.exists():
        log.warning("phase checkpoint %s not found — ensemble runs without it", _path)
_members = [_load_member(path, weight) for path, weight in CHECKPOINTS if path.exists()]
if not _members:
    raise RuntimeError(f"no phase checkpoint found among {[str(p) for p, _ in CHECKPOINTS]}")
_total_weight = sum(m["weight"] for m in _members)
# первая модель — для _build_window (обратная совместимость с бенчмарком и логом)
_model = _members[0]["model"]
WINDOW_SIZE = _members[0]["window"]
FEATURE_VERSION = _members[0]["version"]
log.info("phase ensemble ready: %s", ", ".join(f"{m['name']} x{m['weight']:g}" for m in _members))


def _build_window(history: list[DailyEquipmentCounts], as_of_date: date) -> np.ndarray:
    """One row per calendar day for the first model's `WINDOW_SIZE` days
    ending on `as_of_date` — features.build_window() builds it exactly like
    training did for that checkpoint's feature version (see features.py)."""
    by_day = {h.date: {c.equipment_class: c.count for c in h.counts} for h in history}
    return build_window(by_day, as_of_date, WINDOW_SIZE, EQUIPMENT_CLASSES, FEATURE_VERSION)


@torch.no_grad()
def predict_probs(by_day: dict[date, dict[str, int]], as_of_dates: list[date], batch_size: int = 256) -> np.ndarray:
    """(len(as_of_dates), NUM_PHASES) — ансамбль всех моделей на последней
    позиции окна каждого дня (как в проде). Общий для compute() и бенчмарка."""
    log_sum = np.zeros((len(as_of_dates), NUM_PHASES))
    for m in _members:
        for i in range(0, len(as_of_dates), batch_size):
            chunk = as_of_dates[i : i + batch_size]
            obs = torch.from_numpy(
                np.stack([build_window(by_day, d, m["window"], EQUIPMENT_CLASSES, m["version"]) for d in chunk])
            )
            out = m["model"](
                observations=obs,
                phase_text_embeddings=PHASE_TEXT_EMBEDDINGS.expand(len(chunk), -1, -1),
                phase_structured=PHASE_STRUCTURED.expand(len(chunk), -1, -1),
            )
            log_sum[i : i + len(chunk)] += m["weight"] / _total_weight * torch.log_softmax(out.emissions[:, -1], -1).numpy()
    probs = np.exp(log_sum - log_sum.max(1, keepdims=True))
    return probs / probs.sum(1, keepdims=True)


def compute(command: PhaseCommand) -> PhaseResult:  # EXTENSION POINT
    by_day = {h.date: {c.equipment_class: c.count for c in h.counts} for h in command.history}
    latest = max((h for h in command.history if h.date <= command.as_of_date), key=lambda h: h.date, default=None)
    seen = (
        ", ".join(f"{c.equipment_class}: {c.count}" for c in latest.counts if c.count > 0) or "none"
        if latest is not None
        else "none"
    )
    log.info(
        "classifying phase as of %s (%d days with real data) — latest day: %s",
        command.as_of_date,
        len(command.history),
        seen,
    )

    probs = torch.from_numpy(predict_probs(by_day, [command.as_of_date])[0])
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
        probs={name: float(p) for name, p in zip(PHASE_NAMES, probs.tolist())},
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
