"""Vision service — equipment detection & counting (диаграмма: блок 2).

Consumes `vision.command` from the shared `csm.analysis` exchange, computes
a result, publishes it on `vision.result`. See
backend/integrations/README.md for the pipeline topology this is one leg of.

REAL detector: `weights/best.pt` is a MOCS-trained YOLO model — confirmed by
directly loading it and checking `model.names`, which already returns the
13 MOCS class names natively (`{0: 'worker', 1: 'tower_crane', ...}`), not
COCO's. `COCO_TO_EQUIPMENT` below is therefore an identity map today, kept
around as the seam where a future model with a different class order/naming
would plug in without touching the rest of this file — the "COCO can only
see truck/person" limitation an earlier version of this docstring described
(and cv/a.ipynb's pretrained-COCO detector still has) no longer applies to
this checkpoint. Verified against real construction photos
(phase_determination/data/Строительная техника/) — high-confidence,
varied-class detections (excavator, tower_crane, pile_driver, vehicle_crane,
concrete_mixer, loader, hanging_hook, worker, truck) across samples, not
just the old truck/worker pair.

Run over the file at `command.media_url`. Output is a plain per-class
count, not an arrival/departure event log: a single video/photo is a
snapshot of one moment (the construction phase doesn't change within it),
so there's nothing to gain from timestamping individual detections — just
how much of each equipment class is on site right now. A single photo
(`command.kind == "image"`) has no motion to track, so it's one detection
pass over that frame, each box above the confidence threshold counted once.

Video (`command.kind == "video"`) runs full ByteTrack tracking so the same
physical unit isn't counted once per frame — but a raw "one track, one
count" rule turned out to badly over-count on a real 15-second aerial clip
of a busy site (cv/your_video.mp4): 175 distinct "worker" track ids, most
lasting only a handful of frames — the tracker losing and re-acquiring the
same few (real, but small and distant in an aerial shot) people rather than
175 actual workers. `MIN_TRACK_SECONDS` filters out tracks shorter than
that before counting (175 -> 68 on that same clip). This is a mitigation,
not a precise fix — the frame-count-vs-surviving-tracks curve is a smooth
decline with no clean cutoff between "flicker" and "genuinely brief real
detection", so any threshold is a judgement call, and small/distant objects
in an aerial shot are inherently harder to track reliably than equipment at
closer range.

No zone splitting: `cv/a.ipynb` assigns each detection to a site zone
(`ZONES`/`assign_zone`) and checks per-zone deviations against a schedule.
This service doesn't — a detection is just "this equipment class is present
somewhere in frame", not tied to a location on site. Zones need a
per-project floor plan / camera-to-site mapping that doesn't exist yet; see
backend/integrations/README.md Open items.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import aio_pika
import requests
from ultralytics import YOLO

from schemas import DetectCommand, DetectResult, Envelope, EquipmentCount

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("csm.vision")

RABBITMQ_URL = os.environ.get("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")
EXCHANGE_NAME = "csm.analysis"  # must match backend/integrations/broker.py
COMMAND_ROUTING_KEY = "vision.command"
RESULT_ROUTING_KEY = "vision.result"
COMMAND_QUEUE = "vision.command.q"

WEIGHTS_PATH = Path(__file__).parent / "weights" / "best.pt"
CONF_THRESHOLD = 0.35
TRACKER_CONFIG = "bytetrack.yaml"
# Minimum time a ByteTrack track must persist to count as a real physical
# unit rather than tracker flicker — see _detect()'s comment.
MIN_TRACK_SECONDS = 0.5

COCO_TO_EQUIPMENT: dict[str, str] = {
    "truck": "truck",
    "worker": "worker",
    "tower_crane" :"tower_crane",
    "hanging_hook" : "hanging_hook",
    "vehicle_crane" : "vehicle_crane",
    "roller" : "roller",
    "bulldozer" : "bulldozer",
    "excavator" : "excavator",
    "truck" : "truck",
    "loader" : "loader",
    "pump_truck" : "pump_truck",
    "concrete_mixer" : "concrete_mixer",
    "pile_driver" : "pile_driver",
    "other_vehicle" : "other_vehicle",
}

log.info("loading detector weights from %s", WEIGHTS_PATH)
_model = YOLO(str(WEIGHTS_PATH))


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


def _detect(video_path: Path) -> Counter[str]:
    import cv2

    cap = cv2.VideoCapture(str(video_path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    cap.release()
    min_track_frames = max(1, round(MIN_TRACK_SECONDS * fps))

    track_classes: dict[int, str] = {}
    track_frame_counts: Counter[int] = Counter()

    stream = _model.track(
        source=str(video_path),
        conf=CONF_THRESHOLD,
        tracker=TRACKER_CONFIG,
        stream=True,
        verbose=False,
    )
    for frame_idx, result in enumerate(stream):
        if result.boxes is None:
            continue
        for box in result.boxes:
            class_name = _model.names[int(box.cls[0])]
            equipment_class = COCO_TO_EQUIPMENT.get(class_name)
            if equipment_class is None or box.id is None:
                continue
            track_id = int(box.id[0])
            track_classes[track_id] = equipment_class
            track_frame_counts[track_id] += 1
            x1, y1, x2, y2 = box.xyxy[0].tolist()
            center_x, center_y = (x1 + x2) / 2, (y1 + y2) / 2
            conf = float(box.conf[0]) if box.conf is not None else None
            log.info(
                "found %s (track %d) at frame %d, box center (%.0f, %.0f), conf %.2f",
                equipment_class,
                track_id,
                frame_idx,
                center_x,
                center_y,
                conf if conf is not None else float("nan"),
            )

    # A track shorter than min_track_frames is more likely the tracker
    # losing and re-acquiring the same physical unit (flicker) than a real,
    # distinct, briefly-visible one — see module docstring for the real
    # video this was tuned against (175 -> 68 "distinct" workers just from
    # this filter, on a 15s aerial clip of a busy site).
    counts: Counter[str] = Counter()
    for track_id, n_frames in track_frame_counts.items():
        if n_frames >= min_track_frames:
            counts[track_classes[track_id]] += 1
    return counts


def _detect_photo(image_path: Path) -> Counter[str]:
    counts: Counter[str] = Counter()
    results = _model(str(image_path), conf=CONF_THRESHOLD, verbose=False)
    result = results[0]
    if result.boxes is None:
        return counts

    for detection_id, box in enumerate(result.boxes):
        class_name = _model.names[int(box.cls[0])]
        equipment_class = COCO_TO_EQUIPMENT.get(class_name)
        if equipment_class is None:
            continue
        x1, y1, x2, y2 = box.xyxy[0].tolist()
        center_x, center_y = (x1 + x2) / 2, (y1 + y2) / 2
        conf = float(box.conf[0]) if box.conf is not None else None
        log.info(
            "found %s (detection %d) at box center (%.0f, %.0f), conf %.2f",
            equipment_class,
            detection_id,
            center_x,
            center_y,
            conf if conf is not None else float("nan"),
        )
        counts[equipment_class] += 1
    return counts


def compute(command: DetectCommand) -> DetectResult:  # EXTENSION POINT
    log.info(
        "detecting equipment for asset %s (%s, %s)", command.asset_id, command.kind, command.media_url
    )
    media_path = _download(command.media_url, suffix=".mp4" if command.kind == "video" else ".jpg")
    try:
        if command.kind == "video":
            counts = _detect(media_path)
        else:
            counts = _detect_photo(media_path)
    finally:
        media_path.unlink(missing_ok=True)

    summary = ", ".join(f"{cls}: {n}" for cls, n in sorted(counts.items())) or "none"
    log.info("asset %s: equipment counted — %s", command.asset_id, summary)
    return DetectResult(
        status="done",
        counts=[
            EquipmentCount(equipment_class=cls, count=n) for cls, n in sorted(counts.items())
        ],
    )


async def main() -> None:
    connection = await aio_pika.connect_robust(RABBITMQ_URL)
    async with connection:
        channel = await connection.channel()
        await channel.set_qos(prefetch_count=1)  # detection is slow; don't hog messages
        exchange = await channel.declare_exchange(
            EXCHANGE_NAME, aio_pika.ExchangeType.TOPIC, durable=True
        )
        queue = await channel.declare_queue(COMMAND_QUEUE, durable=True)
        await queue.bind(exchange, COMMAND_ROUTING_KEY)

        async def on_message(message: aio_pika.abc.AbstractIncomingMessage) -> None:
            async with message.process():
                envelope = Envelope[DetectCommand].model_validate_json(message.body)
                try:
                    result = await asyncio.to_thread(compute, envelope.payload)
                except Exception as exc:  # noqa: BLE001 - reported back as a failed result
                    log.exception("compute failed for asset %s", envelope.correlation_id)
                    result = DetectResult(status="failed", error=str(exc))

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
        log.info("vision worker consuming %s from %s", COMMAND_ROUTING_KEY, COMMAND_QUEUE)
        await asyncio.Future()  # run forever


if __name__ == "__main__":
    asyncio.run(main())
