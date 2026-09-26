"""Сравнение весов YOLO на кадрах таймлапсов (data/*.mp4) с ручным эталоном.

Зачем: timelapse_phase_eval.py показал, что на широких ракурсах текущий
детектор (services/vision/weights/best.pt) находит меньше, чем старый
MOCS-чекпоинт (cv/runs/detect/runs_mocs/...). «Находит больше» — ещё не
«находит правильно», поэтому здесь сравнение с эталоном.

Эталон — НАЛИЧИЕ класса на кадре (0/1), а не количество и не боксы: при
640x360 и дальнем ракурсе точно пересчитать рабочих/машины нельзя, а
«есть на кадре башенный кран или нет» размечается уверенно. Размечать по
чистым кадрам (frames/), не глядя на ответы моделей.

Шаги (образ backend-vision, из корня репозитория):
  1. sample  — по --per-video кадров с каждого ролика, равномерно между 10% и
     90% длительности (без титров) -> compare/frames/*.jpg и шаблон
     compare/labels.csv (keep, по колонке на класс).
  2. вручную: keep=0 для кадров без техники / непригодных, в колонках
     классов 1 там, где класс виден.
  3. score   — прогоняет каждую из --weights на кадрах с keep=1 и пишет
     compare/summary.md: precision/recall/F1 по наличию, по классам и в сумме.

    docker run --rm --user "$(id -u):$(id -g)" -e YOLO_CONFIG_DIR=/tmp \\
        -v "$PWD:/repo" -w /repo --entrypoint python backend-vision \\
        phase_determination/compare_detectors.py sample
    docker run ... phase_determination/compare_detectors.py score \\
        --weights services/vision/weights/best.pt cv/runs/detect/runs_mocs/train/weights/best.pt
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VIDEOS_DIR = ROOT / "data"
OUT_DIR = ROOT / "phase_determination" / "timelapse_eval" / "compare"
FRAMES_DIR = OUT_DIR / "frames"
LABELS_CSV = OUT_DIR / "labels.csv"
PREDICTIONS_CSV = OUT_DIR / "predictions.csv"
SUMMARY_MD = OUT_DIR / "summary.md"

# Тот же список и порядок, что EquipmentClass в services/phase/schemas.py
# (и что классы MOCS по индексу).
EQUIPMENT_CLASSES = [
    "worker", "tower_crane", "hanging_hook", "vehicle_crane", "roller", "bulldozer",
    "excavator", "truck", "loader", "pump_truck", "concrete_mixer", "pile_driver",
    "other_vehicle",
]
CONF_THRESHOLD = 0.35  # как CONF_THRESHOLD в services/vision/worker.py


def sample(per_video: int) -> None:
    import cv2  # noqa: PLC0415

    FRAMES_DIR.mkdir(parents=True, exist_ok=True)
    names = []
    for video in sorted(VIDEOS_DIR.glob("*.mp4")):
        cap = cv2.VideoCapture(str(video))
        n_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        for k in range(per_video):
            frac = 0.1 + 0.8 * k / max(per_video - 1, 1)
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(frac * n_frames))
            ok, frame = cap.read()
            if not ok:
                continue
            name = f"{video.stem[:40]}__{round(frac * 100):02d}.jpg"
            cv2.imwrite(str(FRAMES_DIR / name), frame)
            names.append(name)
        cap.release()

    if LABELS_CSV.exists():
        print(f"{LABELS_CSV} уже есть — не перезаписываю")
    else:
        with open(LABELS_CSV, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["frame", "keep", *EQUIPMENT_CLASSES, "note"])
            for name in names:
                writer.writerow([name, "", *([""] * len(EQUIPMENT_CLASSES)), ""])
    print(f"{len(names)} кадров в {FRAMES_DIR}, шаблон {LABELS_CSV}")


def _load_model(weights: Path):
    from ultralytics import YOLO  # noqa: PLC0415

    model = YOLO(str(weights))
    names = [model.names[i] for i in sorted(model.names)]
    if len(names) != len(EQUIPMENT_CLASSES):
        sys.exit(f"{weights}: {len(names)} классов, ожидалось {len(EQUIPMENT_CLASSES)} (MOCS)")
    # старый MOCS-чекпоинт хранит исходные имена ('Static crane', ...) в том же порядке
    return model, dict(zip(names, EQUIPMENT_CLASSES))


def score(weights_list: list[Path]) -> None:
    import cv2  # noqa: PLC0415

    with open(LABELS_CSV, newline="") as f:
        kept = [r for r in csv.DictReader(f) if r["keep"].strip() == "1"]
    truth = {r["frame"]: {c for c in EQUIPMENT_CLASSES if r[c].strip() == "1"} for r in kept}
    labeled = {c for c in EQUIPMENT_CLASSES if any(r[c].strip() for r in kept)}
    if not truth:
        sys.exit(f"в {LABELS_CSV} нет кадров с keep=1")

    preds: dict[str, dict[str, Counter[str]]] = {}
    for weights in weights_list:
        model, class_map = _load_model(weights)
        preds[str(weights)] = {}
        for frame in truth:
            result = model(cv2.imread(str(FRAMES_DIR / frame)), conf=CONF_THRESHOLD, verbose=False)[0]
            preds[str(weights)][frame] = Counter(
                class_map[model.names[int(b.cls[0])]] for b in result.boxes
            )

    with open(PREDICTIONS_CSV, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["weights", "frame", "truth", "predicted"])
        for w, by_frame in preds.items():
            for frame, counts in by_frame.items():
                writer.writerow([w, frame, " ".join(sorted(truth[frame])),
                                 " ".join(f"{c}:{n}" for c, n in sorted(counts.items()))])

    # Класс с пустой колонкой во всех кадрах не размечался (напр. worker —
    # при 360p рабочих не различить) и в оценку не идёт, иначе каждая его
    # детекция посчиталась бы ложной.
    scored = [c for c in EQUIPMENT_CLASSES if c in labeled]
    present = [c for c in scored
               if any(c in t for t in truth.values()) or any(c in p[fr] for p in preds.values() for fr in p)]

    def prf(w: str, classes: list[str]) -> tuple[int, int, int]:
        tp = fp = fn = 0
        for frame, t in truth.items():
            for c in classes:
                hit = preds[w][frame][c] > 0
                tp += hit and c in t
                fp += hit and c not in t
                fn += (not hit) and c in t
        return tp, fp, fn

    def fmt(tp: int, fp: int, fn: int) -> str:
        p = f"{tp / (tp + fp):.0%}" if tp + fp else "—"
        r = f"{tp / (tp + fn):.0%}" if tp + fn else "—"
        f1 = f"{2 * tp / (2 * tp + fp + fn):.2f}" if tp else "0"
        return f"{p} / {r} / {f1}"

    short = {w: Path(w).as_posix() for w in preds}
    lines = [
        "# Сравнение детекторов на кадрах таймлапсов",
        "",
        f"Кадров с эталоном: {len(truth)}. Эталон — наличие класса на кадре (ручная разметка, "
        f"640x360). Порог уверенности {CONF_THRESHOLD}. В ячейках precision / recall / F1 по наличию.",
        "",
        "| класс | в эталоне, кадров | " + " | ".join(short.values()) + " |",
        "|---|---|" + "---|" * len(preds),
    ]
    for c in present:
        n_true = sum(c in t for t in truth.values())
        lines.append(f"| {c} | {n_true} | " + " | ".join(fmt(*prf(w, [c])) for w in preds) + " |")
    lines.append("| **все размеченные классы** | " + str(sum(len(t) for t in truth.values())) + " | "
                 + " | ".join(f"**{fmt(*prf(w, scored))}**" for w in preds) + " |")
    no_worker = [c for c in scored if c != "other_vehicle"]
    lines.append("| техника (без other_vehicle) | "
                 + str(sum(len(t & set(no_worker)) for t in truth.values())) + " | "
                 + " | ".join(fmt(*prf(w, no_worker)) for w in preds) + " |")

    SUMMARY_MD.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"\nзаписано: {PREDICTIONS_CSV}, {SUMMARY_MD}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_sample = sub.add_parser("sample")
    p_sample.add_argument("--per-video", type=int, default=5)
    p_score = sub.add_parser("score")
    p_score.add_argument("--weights", type=Path, nargs="+", required=True)
    args = parser.parse_args()
    if args.cmd == "sample":
        sample(args.per_video)
    else:
        score(args.weights)


if __name__ == "__main__":
    main()
