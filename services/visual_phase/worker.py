"""Visual-phase service — construction phase read directly off a photo,
with no equipment detection in between (диаграмма: extra leg alongside
block 3). Consumes `visual_phase.command` from the shared `csm.analysis`
exchange, publishes a result on `visual_phase.result`. See
backend/integrations/README.md for how this fits next to the equipment-based
`services/phase`.

REAL model, EXPERIMENTAL classifier: `weights/backbone_best.pt` is a DINOv2
ViT-S/14 backbone, self-supervised fine-tuned (SimSiam) on ~10k unlabeled
construction-site photos — see model.py and
phase_determination/visual_state_pretraining.ipynb for how it was trained.
That notebook produces embeddings, not phase labels (no labeled dataset of
"this photo = phase X" exists). `weights/classifier.json` is a nearest-
centroid classifier bolted on top: 8 k-means clusters fit over the
embeddings, each hand-labeled by inspecting its closest photos (see
phase_determination/build_visual_phase_classifier.py's module docstring for
exactly which photos and why). This is a real but rough signal, not a
validated model — treat `confidence` accordingly.

Known, structural coverage gap: the pretraining photo set (HF
`LouisChen15/ConstructionSite`) itself skews toward earthwork/foundation/
structural-frame scenes, so the 8 clusters only ever land on 4 of the 10
canonical phases (Earthwork, Foundation, Structural Frame, External Works —
see `classifier.json`'s `unsupported_phases`). A photo from Masonry, MEP,
Finishing, Preconstruction, Site Preparation or Commissioning will still be
forced onto whichever of those 4 is visually closest — there is no
"unknown" class, because a photo is *always* nearest to *some* centroid.
This is why `backend/analysis.py` originally treated this service's result
as an observational field only. With the linear head (see UPDATE below) it
is fused with `services/phase`'s distribution into the entry's final phase
— see backend/phase_ensemble.py; `probs` in the result carries the full
distribution for that.

`confidence` here is a softmax over negative distances to all 8 centroids
(not a trained probability) — a relative "how much closer is the nearest
cluster than the others", useful for sorting/flagging, not a calibrated
percentage.

UPDATE — linear head (`classifier.json` with `"type": "linear"`): a logistic
regression over the same backbone embeddings, trained on frames of 14
phase-labeled construction timelapses (phase_determination/
visual_phase_timelapse.py). Leave-one-video-out frame accuracy 50.6% vs
24.8% for the k-means classifier above on the same frames, and it covers 9
phases instead of 4 (no Preconstruction — no such footage). Its
`confidence` is a real softmax probability; `cluster_id` is None. Caveat:
trained on fixed-camera timelapse frames, not phone photos. The k-means
classifier is kept as `classifier_kmeans.json` and still loads if
`classifier.json` is swapped back.

UPDATE 2 — ensemble (`"type": "ensemble"`, phase_determination/
build_visual_ensemble.py): trained on 60 labeled timelapse sites instead of
14; three members fused log-linearly with equal weights — the DINOv2 linear
head, a linear head over SigLIP ViT-B-16 embeddings, and SigLIP zero-shot
against text descriptions of all 10 phases. Held-out (10-fold by site,
phase_determination/eval_all.py) this ensemble fused with services/phase
reaches ~66% daily accuracy vs 60.6% with the DINOv2 head alone. The
previous single head is kept as `classifier_linear14.json`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import aio_pika
import numpy as np
import requests
from PIL import Image

from model import VisualPhaseModel
from schemas import Envelope, VisualPhaseCommand, VisualPhaseResult

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("csm.visual_phase")

RABBITMQ_URL = os.environ.get("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")
EXCHANGE_NAME = "csm.analysis"  # must match backend/integrations/broker.py
COMMAND_ROUTING_KEY = "visual_phase.command"
RESULT_ROUTING_KEY = "visual_phase.result"
COMMAND_QUEUE = "visual_phase.command.q"

WEIGHTS_DIR = Path(__file__).parent / "weights"
CHECKPOINT_PATH = WEIGHTS_DIR / "backbone_best.pt"
CLASSIFIER_PATH = WEIGHTS_DIR / "classifier.json"

log.info("loading classifier from %s", CLASSIFIER_PATH)
_classifier = json.loads(CLASSIFIER_PATH.read_text(encoding="utf-8"))
_KIND = _classifier.get("type", "kmeans")  # kmeans | linear | ensemble

log.info("loading backbone from %s", CHECKPOINT_PATH)
_model = VisualPhaseModel(CHECKPOINT_PATH)
_siglip = None
if _KIND == "ensemble" and any(m["backbone"] == "siglip" for m in _classifier["members"]):
    import open_clip  # noqa: PLC0415
    import torch  # noqa: PLC0415

    log.info("loading SigLIP %s (%s)", _classifier["siglip"]["name"], _classifier["siglip"]["pretrained"])
    _siglip_model, _, _siglip_preprocess = open_clip.create_model_and_transforms(
        _classifier["siglip"]["name"], pretrained=_classifier["siglip"]["pretrained"], device="cpu"
    )
    _siglip_model.eval()

    def _siglip(image: Image.Image) -> np.ndarray:
        with torch.no_grad():
            f = _siglip_model.encode_image(_siglip_preprocess(image.convert("RGB")).unsqueeze(0))
            return torch.nn.functional.normalize(f, dim=-1)[0].float().numpy()


def _softmax(logits: np.ndarray) -> np.ndarray:
    e = np.exp(logits - logits.max())
    return e / e.sum()


def _linear(head: dict):
    mean, scale = np.array(head["scaler_mean"], np.float32), np.array(head["scaler_scale"], np.float32)
    coef, intercept = np.array(head["coef"], np.float32), np.array(head["intercept"], np.float32)
    return lambda emb: dict(zip(head["classes"], _softmax(coef @ ((emb - mean) / scale) + intercept).tolist()))


def _zero_shot(zs: dict):
    text = np.array(zs["text_embeddings"], np.float32)
    return lambda emb: dict(zip(zs["classes"], _softmax(zs["logit_scale"] * (text @ emb)).tolist()))


# Участники: (бэкбон, вес, эмбеддинг -> {фаза: вероятность})
if _KIND == "ensemble":
    _members = [
        (m["backbone"], m["weight"], _linear(m["head"]) if "head" in m else _zero_shot(m["zero_shot"]))
        for m in _classifier["members"]
    ]
elif _KIND == "linear":
    _members = [("dino", 1.0, _linear(_classifier))]
else:
    _scaler_mean = np.array(_classifier["scaler_mean"], dtype=np.float32)
    _scaler_scale = np.array(_classifier["scaler_scale"], dtype=np.float32)
    _centroids = np.array(_classifier["centroids"], dtype=np.float32)
    _cluster_phase: dict[int, str] = {int(k): v for k, v in _classifier["cluster_phase"].items()}
    _members = []
log.info(
    "visual phase model ready (%s: %s)",
    _KIND,
    ", ".join(f"{b} x{w:.2f}" for b, w, _ in _members) or f"{len(_centroids)} k-means clusters",
)


def _fuse(dists: list[tuple[float, dict[str, float]]]) -> dict[str, float]:
    """Log-линейное объединение участников. Фаза, которую участник не знает,
    для него нейтральна (среднее по известным) — как в backend/phase_ensemble.py."""
    phases = sorted({p for _, d in dists for p in d})
    log_sum = np.zeros(len(phases))
    total = sum(w for w, _ in dists)
    for w, d in dists:
        neutral = float(np.mean(list(d.values())))
        log_sum += w / total * np.log(np.array([d.get(p, neutral) for p in phases]) + 1e-6)
    probs = _softmax(log_sum)
    return dict(zip(phases, probs.tolist()))


def _download(media_url: str, *, suffix: str) -> Path:
    fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    path = Path(tmp_path)
    with requests.get(media_url, stream=True, timeout=60) as resp:
        resp.raise_for_status()
        with path.open("wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)
    return path


def _middle_frame(video_path: Path) -> Image.Image:
    """One representative frame from the middle of the clip — a construction
    phase doesn't change within a single short video (same assumption
    services/vision's docstring makes), so there's nothing to gain from
    sampling more than one frame."""
    import cv2

    cap = cv2.VideoCapture(str(video_path))
    try:
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_count // 2)
        ok, frame_bgr = cap.read()
        if not ok:
            raise ValueError(f"could not read a frame from {video_path}")
        return Image.fromarray(frame_bgr[:, :, ::-1])
    finally:
        cap.release()


def compute(command: VisualPhaseCommand) -> VisualPhaseResult:  # EXTENSION POINT
    log.info("classifying visual phase for asset %s (%s, %s)", command.asset_id, command.kind, command.media_url)
    media_path = _download(command.media_url, suffix=".mp4" if command.kind == "video" else ".jpg")
    try:
        image = _middle_frame(media_path) if command.kind == "video" else Image.open(media_path)
        image.load()
        dino_embedding = _model.embed(image).numpy()
    finally:
        media_path.unlink(missing_ok=True)

    if _members:
        embeddings = {"dino": dino_embedding}
        if _siglip is not None:
            embeddings["siglip"] = _siglip(image)
        probs = _fuse([(w, head(embeddings[backbone])) for backbone, w, head in _members])
        phase_name = max(probs, key=probs.__getitem__)
        log.info("asset %s -> %s, confidence %.0f%%", command.asset_id, phase_name, probs[phase_name] * 100)
        return VisualPhaseResult(
            status="done", phase_name=phase_name, confidence=probs[phase_name], cluster_id=None, probs=probs
        )

    standardized = (dino_embedding - _scaler_mean) / _scaler_scale
    dists = np.linalg.norm(_centroids - standardized, axis=1)
    # Softmax over negative distances: a relative confidence among the 8
    # clusters, not a calibrated probability - see module docstring.
    weights = np.exp(-dists - (-dists).max())
    probs = weights / weights.sum()
    cluster_id = int(np.argmin(dists))
    phase_name = _cluster_phase[cluster_id]

    log.info(
        "asset %s -> cluster %d (%s), confidence %.0f%%",
        command.asset_id,
        cluster_id,
        phase_name,
        float(probs[cluster_id]) * 100,
    )
    return VisualPhaseResult(
        status="done",
        phase_name=phase_name,
        confidence=float(probs[cluster_id]),
        cluster_id=cluster_id,
        # кластеры одной фазы складываются — бэкенду нужен вектор по фазам
        probs={
            phase: float(sum(p for c, p in enumerate(probs) if _cluster_phase[c] == phase))
            for phase in sorted(set(_cluster_phase.values()))
        },
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
                envelope = Envelope[VisualPhaseCommand].model_validate_json(message.body)
                try:
                    result = await asyncio.to_thread(compute, envelope.payload)
                except Exception as exc:  # noqa: BLE001 - reported back as a failed result
                    log.exception("compute failed for asset %s", envelope.correlation_id)
                    result = VisualPhaseResult(status="failed", error=str(exc))

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
        log.info("visual_phase worker consuming %s from %s", COMMAND_ROUTING_KEY, COMMAND_QUEUE)
        await asyncio.Future()  # run forever


if __name__ == "__main__":
    asyncio.run(main())
