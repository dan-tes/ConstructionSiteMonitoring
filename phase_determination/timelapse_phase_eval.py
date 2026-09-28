"""Проверка модуля 3 (services/phase) на реальных таймлапсах стройки.

Идея: таймлапс — это настоящая последовательность «день за днём» по одной
площадке, т.е. ровно то, что модуль 3 получает в проде как `history`, только
плотнее. Модель же обучалась на синтетике (activities -> expected equipment ->
сгенерированные события), и на реальных счётчиках YOLO её никто не проверял.
Этот скрипт даёт такую проверку — первый шаг перед дообучением на таймлапсах.

Два шага, каждый в своём Docker-образе сервиса (локально torch/ultralytics
не установлены, а так проверяется ровно тот код и те веса, что в проде —
репозиторий монтируется, веса берутся из services/*/weights):

  1. extract  (образ backend-vision) — из каждого ролика в data/*.mp4 берутся
     кадры с частотой --fps по времени ВИДЕО (не по дням: длительность стройки
     в днях заполняется руками позже и не должна требовать повторного
     прогона YOLO), каждый кадр прогоняется детектором сервиса vision с его
     же порогом. Пишет:
       timelapse_eval/detections.csv   — video, t_s, счётчик по 13 классам;
       timelapse_eval/previews/<video>/ — ~12 кадров с боксами на ролик,
                                          чтобы глазами оценить детекцию;
       timelapse_eval/videos.csv        — шаблон (если его ещё нет);
       timelapse_eval/phase_labels.csv  — шаблон (если его ещё нет).

  2. Ручное заполнение:
       videos.csv: project_days — сколько календарных дней стройки покрывает
         ролик (часто есть в названии/описании на YouTube; «15 Months» уже
         подставлено), start_s/end_s — обрезка титров и заставок, use=0 —
         исключить ролик (напр. cinematic-нарезка с разных камер: там время
         не монотонно и «день за днём» не получится).
       phase_labels.csv: для каждой фазы, которая ВИДНА в ролике, — секунда
         видео, с которой она становится основной; фазы, которых в ролике
         нет, оставить пустыми. Фаза момента t = последняя начавшаяся к t.
         Всё до первой отметки не размечено и в метрики не идёт.

  3. evaluate (образ backend-phase) — время видео переводится в дни
     (равномерно между start_s и end_s), кадры одного дня сворачиваются
     медианой по каждому классу, и для каждого дня строится 128-дневное окно
     через `_build_window()` самого сервиса (нули до начала ролика,
     forward-fill пропусков — как в проде). Пишет:
       timelapse_eval/predictions.csv, timelapse_eval/summary.md

Запуск из корня репозитория:
    docker run --rm --user "$(id -u):$(id -g)" -e YOLO_CONFIG_DIR=/tmp \\
        -v "$PWD:/repo" -w /repo --entrypoint python backend-vision \\
        phase_determination/timelapse_phase_eval.py extract
    docker run --rm --user "$(id -u):$(id -g)" -e HF_HOME=/tmp/hf \\
        -v "$PWD:/repo" -w /repo --entrypoint python backend-phase \\
        phase_determination/timelapse_phase_eval.py evaluate
(--user — чтобы результаты не принадлежали root; *_DIR/HF_HOME — потому что
у этого uid внутри образа нет записываемого домашнего каталога.)

ВАЖНО при чтении цифр: ролики 640x360, камера обычно далеко и сверху, а
детектор учился на MOCS (съёмка ближе к земле) — мелкая техника будет
теряться. Поэтому сначала смотреть previews/, потом метрики.
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VIDEOS_DIR = Path(os.environ.get("TIMELAPSE_VIDEOS_DIR", ROOT / "data"))  # data/yt — второй набор
OUT_DIR = ROOT / "phase_determination" / "timelapse_eval"


def _set_out_dir(out_dir: Path) -> None:
    """--out-dir: чтобы прогоны разных весов детектора не затирали друг друга."""
    global OUT_DIR, VIDEOS_CSV, LABELS_CSV, DETECTIONS_CSV, PREVIEWS_DIR, PREDICTIONS_CSV, SUMMARY_MD
    OUT_DIR = out_dir
    VIDEOS_CSV = OUT_DIR / "videos.csv"
    LABELS_CSV = OUT_DIR / "phase_labels.csv"
    DETECTIONS_CSV = OUT_DIR / "detections.csv"
    PREVIEWS_DIR = OUT_DIR / "previews"
    PREDICTIONS_CSV = OUT_DIR / "predictions.csv"
    SUMMARY_MD = OUT_DIR / "summary.md"


_set_out_dir(OUT_DIR)

# Тот же список и порядок, что EquipmentClass в services/phase/schemas.py.
EQUIPMENT_CLASSES = [
    "worker", "tower_crane", "hanging_hook", "vehicle_crane", "roller", "bulldozer",
    "excavator", "truck", "loader", "pump_truck", "concrete_mixer", "pile_driver",
    "other_vehicle",
]
PREVIEWS_PER_VIDEO = 12
AS_OF_BASE = date(2025, 1, 1)  # дата не влияет на модель — важна только форма окна


def _load_service(name: str):
    """Импортирует services/<name>/worker.py как есть (со всей загрузкой весов
    на уровне модуля). extract и evaluate идут в разных процессах/образах,
    так что одноимённые model/schemas/worker разных сервисов не пересекаются."""
    sys.path.insert(0, str(ROOT / "services" / name))
    import worker  # noqa: PLC0415

    return worker


def _canonical_phases() -> list[str]:
    """Фазы в порядке phase_order — из тех же данных, по которым сервис
    phase строит свои PHASE_NAMES (без pandas: в образе vision его нет)."""
    order: dict[str, int] = {}
    with open(ROOT / "services" / "phase" / "data" / "activities_with_equipment.csv", newline="") as f:
        for row in csv.DictReader(f):
            order[row["phase"]] = int(row["phase_order"])
    return sorted(order, key=order.__getitem__)


def _guess_project_days(video_name: str) -> str:
    m = re.search(r"(\d+)_Months", video_name, re.IGNORECASE)
    return str(round(int(m.group(1)) * 30.44)) if m else ""


# ---------------------------------------------------------------------------
# extract
# ---------------------------------------------------------------------------
def extract(sample_fps: float, weights: Path | None, append: bool = False) -> None:
    import cv2  # noqa: PLC0415

    vision = _load_service("vision")
    model, conf = vision._model, vision.CONF_THRESHOLD
    class_map = dict(vision.COCO_TO_EQUIPMENT)
    if weights is not None:
        from ultralytics import YOLO  # noqa: PLC0415

        model = YOLO(str(weights))
        names = [model.names[i] for i in sorted(model.names)]
        if names != EQUIPMENT_CLASSES:
            # Старый MOCS-чекпоинт (cv/runs/detect/runs_mocs) хранит исходные
            # имена MOCS ('Static crane', 'Crane', ...) в том же порядке, что
            # и канонические классы — сопоставляем по индексу.
            if len(names) != len(EQUIPMENT_CLASSES):
                sys.exit(f"{weights}: {len(names)} классов, ожидалось {len(EQUIPMENT_CLASSES)} (MOCS)")
            class_map = dict(zip(names, EQUIPMENT_CLASSES))
            print("классы по индексу:", ", ".join(f"{a} -> {b}" for a, b in class_map.items()))

    videos = sorted(VIDEOS_DIR.glob("*.mp4"))
    if not videos:
        sys.exit(f"нет роликов в {VIDEOS_DIR}")
    # ролики, заранее помеченные use=0 в videos.csv, не прогоняем
    if VIDEOS_CSV.exists():
        with open(VIDEOS_CSV, newline="") as f:
            skip = {row["video"] for row in csv.DictReader(f) if row["use"].strip() != "1"}
        videos = [v for v in videos if v.name not in skip]
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # --append: дописать только ролики, которых ещё нет в detections.csv
    done: set[str] = set()
    if append and DETECTIONS_CSV.exists():
        with open(DETECTIONS_CSV, newline="") as f:
            done = {row["video"] for row in csv.DictReader(f)}
        videos = [v for v in videos if v.name not in done]
        print(f"уже обработано {len(done)}, новых: {len(videos)}", flush=True)

    durations: dict[str, float] = {}
    with open(DETECTIONS_CSV, "a" if done else "w", newline="") as f:
        writer = csv.writer(f)
        if not done:
            writer.writerow(["video", "t_s", *EQUIPMENT_CLASSES])
        for video in videos:
            cap = cv2.VideoCapture(str(video))
            fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
            n_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            durations[video.name] = n_frames / fps
            step = max(1, round(fps / sample_fps))
            sample_idx = list(range(0, n_frames, step))
            preview_every = max(1, len(sample_idx) // PREVIEWS_PER_VIDEO)
            preview_dir = PREVIEWS_DIR / video.stem
            preview_dir.mkdir(parents=True, exist_ok=True)

            totals: Counter[str] = Counter()
            frame_idx, n_done = 0, 0
            for target in sample_idx:
                while frame_idx < target:  # grab() без декодирования — быстро
                    cap.grab()
                    frame_idx += 1
                ok, frame = cap.read()
                frame_idx += 1
                if not ok:
                    break
                result = model(frame, conf=conf, verbose=False)[0]
                counts: Counter[str] = Counter()
                for box in result.boxes if result.boxes is not None else []:
                    cls = class_map.get(model.names[int(box.cls[0])])
                    if cls is not None:
                        counts[cls] += 1
                totals.update(counts)
                t_s = target / fps
                writer.writerow([video.name, f"{t_s:.2f}", *(counts[c] for c in EQUIPMENT_CLASSES)])
                if n_done % preview_every == 0:
                    cv2.imwrite(str(preview_dir / f"t{t_s:07.1f}s.jpg"), result.plot())
                n_done += 1
            cap.release()

            seen = ", ".join(f"{c} {n / max(n_done, 1):.1f}" for c, n in totals.most_common()) or "ничего"
            print(f"{video.name}: {n_done} кадров; в среднем на кадр: {seen}", flush=True)

    # шаблоны: новые ролики дописываются, уже заполненные строки не трогаются
    listed: set[str] = set()
    if VIDEOS_CSV.exists():
        with open(VIDEOS_CSV, newline="") as f:
            listed = {row["video"] for row in csv.DictReader(f)}
    new = {n: d for n, d in durations.items() if n not in listed}
    if new:
        with open(VIDEOS_CSV, "a" if listed else "w", newline="") as f:
            writer = csv.writer(f)
            if not listed:
                writer.writerow(["video", "duration_s", "project_days", "start_s", "end_s", "use"])
            for name, dur in new.items():
                writer.writerow([name, f"{dur:.1f}", _guess_project_days(name), "0", f"{dur:.1f}", "1"])
        print(f"шаблон: {VIDEOS_CSV} (+{len(new)})")
    if not LABELS_CSV.exists():
        with open(LABELS_CSV, "w", newline="") as f:
            csv.writer(f).writerow(["video", "phase", "start_s"])
        print(f"шаблон: {LABELS_CSV}")
    print(f"готово: {DETECTIONS_CSV}, превью в {PREVIEWS_DIR}")


# ---------------------------------------------------------------------------
# evaluate
# ---------------------------------------------------------------------------
def _read_videos() -> dict[str, dict]:
    videos = {}
    with open(VIDEOS_CSV, newline="") as f:
        for row in csv.DictReader(f):
            if row["use"].strip() != "1":
                continue
            if not row["project_days"].strip():
                print(f"пропуск {row['video']}: не заполнен project_days")
                continue
            videos[row["video"]] = {
                "project_days": int(float(row["project_days"])),
                "start_s": float(row["start_s"]),
                "end_s": float(row["end_s"]),
            }
    return videos


def _read_labels(phase_names: list[str]) -> dict[str, list[tuple[float, str]]]:
    labels: dict[str, list[tuple[float, str]]] = defaultdict(list)
    with open(LABELS_CSV, newline="") as f:
        for row in csv.DictReader(f):
            if not row["start_s"].strip():
                continue
            if row["phase"] not in phase_names:
                sys.exit(f"{LABELS_CSV}: неизвестная фаза {row['phase']!r}, допустимы: {phase_names}")
            labels[row["video"]].append((float(row["start_s"]), row["phase"]))
    return {v: sorted(marks) for v, marks in labels.items()}


def _label_at(marks: list[tuple[float, str]], t_s: float) -> str | None:
    current = None
    for start, phase in marks:
        if start <= t_s:
            current = phase
    return current


def load_series(phase_names: list[str]) -> dict[str, dict]:
    """Размеченные ролики как ряды «по дням»: video -> {
        "days": длительность в днях,
        "counts": {день: {класс: счётчик}} — только дни, на которые попал кадр;
                  несколько кадров одного дня сворачиваются медианой, чтобы
                  единичный промах/ложняк YOLO не становился «состоянием дня»,
        "labels": [фаза эталона или None] на каждый день,
        "t_mid": [секунда видео, соответствующая середине дня]}.
    Общий код для evaluate и finetune_timelapse.py."""
    videos = _read_videos()
    labels = _read_labels(phase_names)
    samples: dict[str, list[tuple[float, dict[str, int]]]] = defaultdict(list)
    with open(DETECTIONS_CSV, newline="") as f:
        for row in csv.DictReader(f):
            samples[row["video"]].append((float(row["t_s"]), {c: int(row[c]) for c in EQUIPMENT_CLASSES}))

    series = {}
    for video, meta in videos.items():
        if video not in labels:
            print(f"пропуск {video}: нет разметки фаз в {LABELS_CSV.name}")
            continue
        days, span = meta["project_days"], meta["end_s"] - meta["start_s"]
        by_day: dict[int, list[dict[str, int]]] = defaultdict(list)
        for t_s, counts in samples[video]:
            if meta["start_s"] <= t_s <= meta["end_s"]:
                by_day[min(days - 1, int((t_s - meta["start_s"]) / span * days))].append(counts)
        t_mid = [meta["start_s"] + (d + 0.5) / days * span for d in range(days)]
        series[video] = {
            "days": days,
            "counts": {
                d: {c: n for c in EQUIPMENT_CLASSES if (n := round(statistics.median(fc[c] for fc in frame_counts))) > 0}
                for d, frame_counts in sorted(by_day.items())
            },
            "labels": [_label_at(labels[video], t) for t in t_mid],
            "t_mid": t_mid,
        }
    return series


def evaluate(batch_size: int) -> None:
    phase = _load_service("phase")
    phase_names = phase.PHASE_NAMES
    phase_order = {p: i for i, p in enumerate(phase_names)}

    rows = []
    for video, sr in load_series(phase_names).items():
        # тот же вход, что compute() собирает из PhaseCommand.history
        by_day = {AS_OF_BASE + timedelta(days=d): counts for d, counts in sr["counts"].items()}
        days = [d for d in range(sr["days"]) if sr["labels"][d] is not None]
        probs = phase.predict_probs(by_day, [AS_OF_BASE + timedelta(days=d) for d in days], batch_size)
        for d, p in zip(days, probs):
            k = int(p.argmax())
            rows.append({
                "video": video, "day": d, "t_s": round(sr["t_mid"][d], 2), "truth": sr["labels"][d],
                "has_frame": d in sr["counts"], "pred": phase_names[k], "confidence": float(p[k]),
            })

    if not rows:
        sys.exit("нечего оценивать: заполните videos.csv (project_days) и phase_labels.csv")

    _write_report(rows, phase_names, phase_order)


def _write_report(rows: list[dict], phase_names: list[str], phase_order: dict[str, int]) -> None:
    with open(PREDICTIONS_CSV, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["video", "day", "t_s", "truth", "pred", "confidence", "has_frame"])
        for r in rows:
            writer.writerow([r["video"], r["day"], r["t_s"], r["truth"], r["pred"],
                             f"{r['confidence']:.3f}", int(r["has_frame"])])

    def acc(rs):
        return sum(r["pred"] == r["truth"] for r in rs) / len(rs)

    def acc_pm1(rs):
        return sum(abs(phase_order[r["pred"]] - phase_order[r["truth"]]) <= 1 for r in rs) / len(rs)

    lines = [
        "# Модуль 3 на таймлапсах",
        "",
        f"Размеченных дней: {len(rows)}, роликов: {len({r['video'] for r in rows})}. "
        "Метрики по дням (каждый день ролика — одно предсказание на 128-дневном окне).",
        "",
        f"- точность: **{acc(rows):.1%}**",
        f"- точность ±1 фаза по порядку: {acc_pm1(rows):.1%}",
        f"- средняя уверенность: {statistics.mean(r['confidence'] for r in rows):.0%}",
        "- для сравнения, константа «самая частая фаза эталона» ({}): {:.1%}".format(
            *max(((p, sum(r["truth"] == p for r in rows) / len(rows)) for p in phase_names), key=lambda t: t[1])
        ),
        "",
        "## По роликам",
        "",
        "| ролик | дней | точность | ±1 фаза | чаще всего предсказано |",
        "|---|---|---|---|---|",
    ]
    by_video: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_video[r["video"]].append(r)
    for video, rs in by_video.items():
        top = ", ".join(f"{p} {n / len(rs):.0%}" for p, n in Counter(r["pred"] for r in rs).most_common(2))
        lines.append(f"| {video} | {len(rs)} | {acc(rs):.0%} | {acc_pm1(rs):.0%} | {top} |")

    lines += ["", "## По фазам", "", "| фаза | дней в эталоне | recall | precision |", "|---|---|---|---|"]
    for p in phase_names:
        truth_p = [r for r in rows if r["truth"] == p]
        pred_p = [r for r in rows if r["pred"] == p]
        if not truth_p and not pred_p:
            continue
        recall = f"{acc(truth_p):.0%}" if truth_p else "—"
        precision = f"{sum(r['truth'] == p for r in pred_p) / len(pred_p):.0%}" if pred_p else "—"
        lines.append(f"| {p} | {len(truth_p)} | {recall} | {precision} |")

    used = [p for p in phase_names if any(r["truth"] == p or r["pred"] == p for r in rows)]
    lines += ["", "## Матрица ошибок (строки — эталон, столбцы — предсказание)", "",
              "| | " + " | ".join(used) + " |", "|---" * (len(used) + 1) + "|"]
    confusion = Counter((r["truth"], r["pred"]) for r in rows)
    for t in used:
        lines.append(f"| {t} | " + " | ".join(str(confusion[(t, p)] or "") for p in used) + " |")

    SUMMARY_MD.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"\nзаписано: {PREDICTIONS_CSV}, {SUMMARY_MD}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_extract = sub.add_parser("extract", help="кадры -> YOLO -> detections.csv (образ backend-vision)")
    p_extract.add_argument("--fps", type=float, default=2.0, help="кадров на секунду ВИДЕО")
    p_extract.add_argument("--weights", type=Path, help="другие веса YOLO вместо services/vision/weights/best.pt")
    p_extract.add_argument("--append", action="store_true", help="обработать только ролики, которых ещё нет в detections.csv")
    p_eval = sub.add_parser("evaluate", help="окна -> модуль 3 -> summary.md (образ backend-phase)")
    p_eval.add_argument("--batch-size", type=int, default=256)
    for p in (p_extract, p_eval):
        p.add_argument("--out-dir", type=Path, default=OUT_DIR)
    args = parser.parse_args()
    _set_out_dir(args.out_dir.resolve())

    if args.cmd == "extract":
        extract(args.fps, args.weights, args.append)
    else:
        evaluate(args.batch_size)


if __name__ == "__main__":
    main()
