"""Дообучение модуля 3 v2 на реальных таймлапсах (data/*.mp4) с честной
оценкой leave-one-video-out.

v2 (train_phase_v2.py) учится только на синтетике + симуляторе детектора.
Здесь к ней подмешиваются настоящие ряды «день -> счётчики YOLO» из 14
размеченных таймлапсов (timelapse_phase_eval.py: detections.csv,
phase_labels.csv, videos.csv). Синтетика остаётся в каждой эпохе (50/50),
чтобы модель не забыла фазы, которых в роликах почти нет.

Детекции берутся из ДВУХ прогонов YOLO — текущего прод-детектора
(timelapse_eval/) и старого MOCS-чекпоинта (timelapse_eval_old_yolo/) —
каждое окно случайно из одного из них: модель не должна подстраиваться
под ошибки одного конкретного детектора (в проде стоит текущий).

Оценка: для каждого ролика модель дообучается на остальных 13 и
предсказывает каждый размеченный день отложенного — так же, как прод и
бенчмарк (окно через features.build_window, последняя позиция). Отчёты:
timelapse_eval[_old_yolo]/finetune_lovo/summary.md. Итоговая модель
дообучается на всех 14 роликах -> construction_phase_checkpoints_v2_ft/best.pt;
её ожидаемое качество на новой стройке — это и есть LOVO-цифры, а не
прогон на тех же роликах.

ВАЖНО: эталон фаз и длительности строек размечены вручную по контактным
листам (timelapse_eval/contact/), длительности — оценка.

Запуск (GPU-окружение ~/.venvs/csm-gpu, из корня репозитория):
    ~/.venvs/csm-gpu/bin/python phase_determination/finetune_timelapse.py
"""
from __future__ import annotations

import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import torch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "services" / "phase"))
sys.path.insert(0, str(ROOT / "phase_determination"))
import timelapse_phase_eval as tpe  # noqa: E402
import train_phase_v2 as t2  # noqa: E402
from features import FEATURE_VERSION, build_phase_meta, build_phase_structured, build_window, dense_history, encode_v2  # noqa: E402
from model import ConstructionPhaseModel  # noqa: E402

BASE_CHECKPOINT = Path(os.environ.get("BASE_CHECKPOINT", t2.DATA_DIR / "construction_phase_checkpoints_v2" / "best.pt"))
OUT_CHECKPOINT = Path(os.environ.get("FT_OUT", t2.DATA_DIR / "construction_phase_checkpoints_v2_ft" / "best.pt"))
PD = ROOT / "phase_determination"
# имя детектора -> папки timelapse_phase_eval (detections/labels/videos);
# FT_WITH_YT=1 добавляет второй набор роликов (data/yt, 46 строек)
DETECTION_SETS = {
    "new_yolo": [PD / "timelapse_eval"] + ([PD / "timelapse_eval_yt"] if os.environ.get("FT_WITH_YT") else []),
    "old_yolo": [PD / "timelapse_eval_old_yolo"] + ([PD / "timelapse_eval_yt_old_yolo"] if os.environ.get("FT_WITH_YT") else []),
}
# куда писать отчёты/вероятности held-out прогона: по умолчанию — в первую папку набора
REPORT_DIRS = {
    name: Path(os.environ["FT_REPORT_ROOT"]) / name if os.environ.get("FT_REPORT_ROOT") else folders[0]
    for name, folders in DETECTION_SETS.items()
}
# 0 — leave-one-video-out; N — N групп роликов (быстрее на большом наборе)
FOLDS = int(os.environ.get("FT_FOLDS", 0))
EPOCHS = int(os.environ.get("FT_EPOCHS", 6))
REAL_PER_EPOCH = int(os.environ.get("FT_REAL", 3000))
SYN_PER_EPOCH = int(os.environ.get("FT_SYN", 6000))  # 2:1 к реальным — см. сравнение 1:1 в README/истории
LEARNING_RATE = 1e-4
BASE_DATE = date(2025, 1, 1)
MONITORING_FROM_START_PROB = 0.8  # таймлапс-камеру обычно ставят в начале


def real_sample(rng, sr, phase_to_id, classes):
    """Окно из реального ролика — с той же «редкостью снимков», что и
    синтетические окна, чтобы модель не привыкла к ежедневным кадрам."""
    W = t2.WINDOW_SIZE
    labeled = [d for d, lab in enumerate(sr["labels"]) if lab is not None]
    t_end = int(rng.choice(labeled))
    mon = 0 if rng.random() < MONITORING_FROM_START_PROB else int(rng.integers(0, t_end + 1))
    days = [d for d in sr["counts"] if mon <= d <= t_end]
    if rng.random() >= t2.DAILY_CADENCE_PROB:
        k = rng.integers(2, 11)
        days = [d for d in days if rng.random() < 1.0 / k]
    history = {BASE_DATE + timedelta(days=d): sr["counts"][d] for d in days}
    x = encode_v2(*dense_history(history, BASE_DATE + timedelta(days=t_end), W, classes))
    y = np.full(W, -100, dtype=np.int64)
    for j in range(W):
        d = t_end - W + 1 + j
        if d >= 0 and sr["labels"][d] is not None and x[j, len(classes) + 1] > 0:  # monitoring_started
            y[j] = phase_to_id[sr["labels"][d]]
    return x, y


def load_all_series(phase_names):
    series = {}
    for name, folders in DETECTION_SETS.items():
        series[name] = {}
        for folder in folders:
            tpe._set_out_dir(folder)
            series[name].update(tpe.load_series(phase_names))
    videos = sorted(set.intersection(*(set(s) for s in series.values())))
    return series, videos


def make_set(rng, series, videos, sim, projects, syn_ids, phase_to_id, classes):
    xs, ys = [], []
    names = list(series)
    for _ in range(REAL_PER_EPOCH):
        sr = series[names[rng.integers(0, len(names))]][videos[rng.integers(0, len(videos))]]
        x, y = real_sample(rng, sr, phase_to_id, classes)
        xs.append(x)
        ys.append(y)
    Xs, Ys = t2.make_batch_set(sim, projects, syn_ids, SYN_PER_EPOCH, rng)
    X = torch.cat([torch.from_numpy(np.stack(xs)), Xs])
    Y = torch.cat([torch.from_numpy(np.stack(ys)), Ys])
    return X, Y


def new_model(base, config):
    model = ConstructionPhaseModel(
        observation_dim=config["observation_dim"],
        phase_text_dim=config["phase_text_dim"],
        phase_structured_dim=config["phase_structured_dim"],
        max_len=config["window_size"],
        causal=config["causal"],
    )
    model.load_state_dict(base["model_state_dict"])
    return model.to(t2.DEVICE)


def finetune(model, rng, series, videos, sim, projects, syn_ids, phase_to_id, classes, text, structured):
    optimizer = torch.optim.AdamW(model.parameters(), lr=LEARNING_RATE, weight_decay=t2.WEIGHT_DECAY)
    for _ in range(EPOCHS):
        X, Y = make_set(rng, series, videos, sim, projects, syn_ids, phase_to_id, classes)
        t2.run(model, X, Y, text, structured, optimizer)
    return model


@torch.no_grad()
def predict_video(model, sr, phase_names, classes, text, structured, window):
    history = {BASE_DATE + timedelta(days=d): c for d, c in sr["counts"].items()}
    days = [d for d, lab in enumerate(sr["labels"]) if lab is not None]
    model.eval()
    rows = []
    for i in range(0, len(days), 256):
        chunk = days[i : i + 256]
        obs = torch.from_numpy(
            np.stack([build_window(history, BASE_DATE + timedelta(days=d), window, classes, FEATURE_VERSION) for d in chunk])
        ).to(t2.DEVICE)
        out = model(obs, text.expand(len(chunk), -1, -1), structured.expand(len(chunk), -1, -1))
        probs = torch.softmax(out.emissions[:, -1], dim=-1).cpu()
        for d, p in zip(chunk, probs):
            k = int(p.argmax())
            rows.append({"day": d, "t_s": round(sr["t_mid"][d], 2), "truth": sr["labels"][d], "pred": phase_names[k],
                         "confidence": float(p[k]), "has_frame": d in sr["counts"], "probs": p.numpy()})
    return rows


def main() -> None:
    t2.set_seed(t2.SEED)
    base = torch.load(BASE_CHECKPOINT, map_location="cpu")
    config = base["config"]
    assert config.get("feature_version") == FEATURE_VERSION, "нужен v2-чекпоинт (train_phase_v2.py)"

    import pandas as pd  # noqa: PLC0415
    from sentence_transformers import SentenceTransformer  # noqa: PLC0415

    phase_names, classes, prevalence, phase_meta = build_phase_meta(t2.DATA_DIR)
    phase_to_id = {p: i for i, p in enumerate(phase_names)}
    encoder = SentenceTransformer(t2.TEXT_MODEL_NAME, device="cpu")
    text = encoder.encode(phase_meta["phase_text"].tolist(), convert_to_tensor=True, normalize_embeddings=True)
    text = text.float().unsqueeze(0).to(t2.DEVICE)
    structured = torch.from_numpy(build_phase_structured(phase_names, prevalence, phase_meta)).unsqueeze(0).to(t2.DEVICE)

    activities = pd.read_csv(t2.DATA_DIR / "activities_with_equipment.csv")
    events = pd.read_csv(t2.DATA_DIR / "equipment_events_with_context.csv")
    events["at"] = pd.to_datetime(events["at"])
    projects = t2.build_projects(activities, events, classes, phase_to_id)
    split = activities[["project_id", "split"]].drop_duplicates().set_index("project_id")["split"]
    syn_ids = [p for p in projects if split[p] == "train"]
    sim = t2.Simulator(classes)

    series, videos = load_all_series(phase_names)
    print(f"устройство {t2.DEVICE}; роликов: {len(videos)}; эпох {EPOCHS} x ({REAL_PER_EPOCH} реальных + {SYN_PER_EPOCH} синтетических)", flush=True)

    # --- held-out: leave-one-video-out или FOLDS групп роликов ---
    shuffled = list(np.random.default_rng(t2.SEED).permutation(videos))
    groups = [[v] for v in videos] if FOLDS <= 0 else [shuffled[i::FOLDS] for i in range(FOLDS)]
    lovo_rows = {name: [] for name in series}
    for k, held_out in enumerate(groups):
        t0 = time.time()
        rng = np.random.default_rng(t2.SEED + k)
        model = finetune(new_model(base, config), rng, series, [v for v in videos if v not in held_out],
                         sim, projects, syn_ids, phase_to_id, classes, text, structured)
        accs = []
        for name in series:
            fold_rows = []
            for held in held_out:
                rows = predict_video(model, series[name][held], phase_names, classes, text, structured, config["window_size"])
                for r in rows:
                    r["video"] = held
                fold_rows += rows
            lovo_rows[name] += fold_rows
            accs.append(f"{name} {sum(r['truth'] == r['pred'] for r in fold_rows) / len(fold_rows):.0%}")
        label = held_out[0][:45] if len(held_out) == 1 else f"{len(held_out)} роликов"
        print(f"[{k + 1}/{len(groups)}] {label}: {', '.join(accs)} ({time.time() - t0:.0f}s)", flush=True)

    order = {p: i for i, p in enumerate(phase_names)}
    for name, folder in REPORT_DIRS.items():
        tpe._set_out_dir(folder / "finetune_lovo")
        tpe.OUT_DIR.mkdir(parents=True, exist_ok=True)
        print(f"\n===== LOVO, детекции {name} =====")
        tpe._write_report(lovo_rows[name], phase_names, order)
        # полные вероятности по дням — для объединения с визуальным сигналом
        # (visual_phase_timelapse.py)
        np.savez(
            tpe.OUT_DIR / "probs.npz",
            video=np.array([r["video"] for r in lovo_rows[name]]),
            day=np.array([r["day"] for r in lovo_rows[name]]),
            t_s=np.array([r["t_s"] for r in lovo_rows[name]]),
            probs=np.stack([r["probs"] for r in lovo_rows[name]]),
            phase_names=np.array(phase_names),
        )

    # --- итоговая модель на всех роликах ---
    model = finetune(new_model(base, config), np.random.default_rng(t2.SEED), series, videos,
                     sim, projects, syn_ids, phase_to_id, classes, text, structured)
    OUT_CHECKPOINT.parent.mkdir(parents=True, exist_ok=True)
    torch.save(
        {
            **{k: v for k, v in base.items() if k not in ("model_state_dict", "optimizer_state_dict")},
            "model_state_dict": {k: v.cpu() for k, v in model.state_dict().items()},
            "finetune": {"base": str(BASE_CHECKPOINT), "videos": videos, "epochs": EPOCHS, "folds": FOLDS,
                         "real_per_epoch": REAL_PER_EPOCH, "syn_per_epoch": SYN_PER_EPOCH, "lr": LEARNING_RATE},
        },
        OUT_CHECKPOINT,
    )
    print(f"\nитоговая модель: {OUT_CHECKPOINT}")


if __name__ == "__main__":
    main()
