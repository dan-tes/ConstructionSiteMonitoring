"""Vision service — equipment detection & tracking (диаграмма: блок 2).

Consumes `vision.command` from the shared `csm.analysis` exchange, computes
a result, publishes it on `vision.result`. See
backend/integrations/README.md for the pipeline topology this is one leg of.

REAL detector: YOLOv8m pretrained on COCO (weights/yolov8m.pt, same file as
cv/yolov8m.pt), run over the file at `command.media_url`. Video
(`command.kind == "video"`) gets full ByteTrack tracking, one "arrival" event
per track. A single photo (`command.kind == "image"`) has no motion to
track, so it's one detection pass over that frame — every box above the
confidence threshold is its own "arrival" event, tagged with a per-detection
index instead of a tracker id (there's nothing to track across frames of a
single photo).
Known limitation, same one noted in cv/a.ipynb: COCO's classes only overlap
the MOCS equipment set (see schemas.EquipmentClass) on "truck" and "person"
("worker") — nothing else the model can see (excavator, crane, bulldozer,
...) has a COCO class to map from. `COCO_TO_EQUIPMENT` below is the
exhaustive mapping; everything else is dropped rather than guessed. Swap in
a MOCS-trained detector (cv/b.ipynb is the training pipeline for one) to see
the rest of the equipment classes — that's this service's real EXTENSION
POINT, `compute()`'s signature (DetectCommand -> DetectResult) won't need to
change.

No zone splitting: `cv/a.ipynb` assigns each detection to a site zone
(`ZONES`/`assign_zone`) and checks per-zone deviations against a schedule.
This service doesn't — a detection is just "this equipment class arrived
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
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aio_pika
import requests
from ultralytics import YOLO

from schemas import DetectCommand, DetectResult, Envelope, EquipmentEvent

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("csm.vision")

RABBITMQ_URL = os.environ.get("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")
EXCHANGE_NAME = "csm.analysis"  # must match backend/integrations/broker.py
COMMAND_ROUTING_KEY = "vision.command"
RESULT_ROUTING_KEY = "vision.result"
COMMAND_QUEUE = "vision.command.q"

WEIGHTS_PATH = Path(__file__).parent / "weights" / "best.pt"
CONF_THRESHOLD = 0.1
TRACKER_CONFIG = "bytetrack.yaml"

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


def _detect(video_path: Path, recorded_at: datetime) -> list[EquipmentEvent]:
    import cv2

    cap = cv2.VideoCapture(str(video_path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    cap.release()

    events: list[EquipmentEvent] = []
    seen_tracks: set[int] = set()

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
            if track_id in seen_tracks:
                continue  # one "arrival" per track — see module docstring
            seen_tracks.add(track_id)
            timestamp = recorded_at + timedelta(seconds=frame_idx / fps)
            x1, y1, x2, y2 = box.xyxy[0].tolist()
            center_x, center_y = (x1 + x2) / 2, (y1 + y2) / 2
            conf = float(box.conf[0]) if box.conf is not None else None
            log.info(
                "found %s (track %d) at frame %d / %s, box center (%.0f, %.0f), conf %.2f",
                equipment_class,
                track_id,
                frame_idx,
                timestamp.isoformat(),
                center_x,
                center_y,
                conf if conf is not None else float("nan"),
            )
            events.append(
                EquipmentEvent(
                    track_id=track_id,
                    equipment_class=equipment_class,
                    event="arrival",
                    at=timestamp,
                )
            )
    return events


def _detect_photo(image_path: Path, recorded_at: datetime) -> list[EquipmentEvent]:
    events: list[EquipmentEvent] = []
    results = _model(str(image_path), conf=CONF_THRESHOLD, verbose=False)
    result = results[0]
    if result.boxes is None:
        return events

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
        events.append(
            EquipmentEvent(
                track_id=detection_id,
                equipment_class=equipment_class,
                event="arrival",
                at=recorded_at,
            )
        )
    return events


def compute(command: DetectCommand) -> DetectResult:  # EXTENSION POINT
    log.info(
        "detecting equipment for asset %s (%s, %s)", command.asset_id, command.kind, command.media_url
    )
    media_path = _download(command.media_url, suffix=".mp4" if command.kind == "video" else ".jpg")
    try:
        if command.kind == "video":
            events = _detect(media_path, command.recorded_at)
        else:
            events = _detect_photo(media_path, command.recorded_at)
    finally:
        media_path.unlink(missing_ok=True)

    by_class = Counter(e.equipment_class for e in events)
    summary = ", ".join(f"{cls}: {n}" for cls, n in sorted(by_class.items())) or "none"
    log.info(
        "asset %s: %d equipment arrivals detected — %s",
        command.asset_id,
        len(events),
        summary,
    )
    log.info(DetectResult(status="done", events=events))
    return DetectResult(status="done", events=events)


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
