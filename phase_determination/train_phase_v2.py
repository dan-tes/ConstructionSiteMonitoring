"""Обучение модуля 3 (ConstructionPhaseModel) v2 — на том, что прод реально
получает от детектора, а не на истинном состоянии площадки.

Что было не так у v1 (construction_phase_training.ipynb, ячейка 12) и что
здесь исправлено — каждое расхождение найдено на бенчмарке по таймлапсам
(timelapse_phase_eval.py: v1 угадывает фазу в ~20% дней при 37% у
константы «Structural Frame»):

  1. Счётчик в обучении был ИСТИННЫМ (шум трогал только visible/confidence),
     а в проде счётчик — выход YOLO. Здесь счётчик проходит через симулятор
     детектора `simulate_detections()`: попадание каждой единицы техники с
     вероятностью recall класса, ложные срабатывания, путаница классов,
     «фоновые» объекты вне стройки (дальние краны, парковка) — параметры
     взяты из ручной разметки 53 кадров таймлапсов
     (timelapse_eval/compare/summary.md, старый MOCS-детектор: recall
     башенного крана ~0.8 на кадр, автокрана ~0.2, сваебоя ~0).
  2. Синтетика даёт счётчики только 0/1, на реальных кадрах бывает 4-5
     кранов — добавлена кратность техники по проекту.
  3. Обучение шло на плотных ежедневных данных, прод — на редких снимках с
     forward-fill и нулями до первого снимка. Здесь снимки редкие (от
     ежедневных до раза в 3 недели), окна начинаются и до старта
     мониторинга, признаки строятся features.encode_v2 — тем же кодом, что
     в проде.
  4. Трансформер без маски видел «будущее» внутри окна, а прод берёт
     последнюю позицию. Здесь каузальная маска: каждая позиция учится как
     «последний день» своей истории; лосс только по дням после первого снимка.

Данные (activities / events / descriptions) и разбиение train/val/test по
проектам — те же, что у v1. Архитектура та же, меняется только
observation_dim (39 -> 16, см. features.py) и causal=True.

Запуск (образ backend-phase, CPU; ~20-30 мин):
    docker run --rm --user "$(id -u):$(id -g)" -e HF_HOME=/tmp/hf \\
        -v "$PWD:/repo" -w /repo --entrypoint python backend-phase \\
        phase_determination/train_phase_v2.py
Выход: phase_determination/data/construction_phase_checkpoints_v2/best.pt
"""
from __future__ import annotations

import math
import os
import random
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import torch.nn.functional as F

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "services" / "phase"))
from features import (  # noqa: E402
    FEATURE_VERSION,
    build_phase_meta,
    build_phase_structured,
    encode_v2,
    observation_dim,
)
from model import ConstructionPhaseModel  # noqa: E402

SEED = 42
DATA_DIR = ROOT / "phase_determination" / "data"
OUT_DIR = Path(os.environ.get("OUT_DIR", DATA_DIR / "construction_phase_checkpoints_v2"))
TEXT_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"

WINDOW_SIZE = 128
# переопределяются через env для быстрого смоук-теста
TRAIN_SAMPLES_PER_EPOCH = int(os.environ.get("TRAIN_SAMPLES", 24_000))
EVAL_SAMPLES = int(os.environ.get("EVAL_SAMPLES", 3_000))
EPOCHS = int(os.environ.get("EPOCHS", 25))
BATCH_SIZE = 64
LEARNING_RATE = 3e-4
DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")
# продолжить с сохранённого чекпоинта (веса + оптимизатор, если сохранён) —
# косинусное расписание продолжается с эпохи чекпоинта
RESUME_FROM = os.environ.get("RESUME_FROM")
WEIGHT_DECAY = 1e-4

# --- Симулятор детектора ----------------------------------------------------
# класс: (recall одной единицы, ложных срабатываний в среднем на снимок)
DETECTOR = {
    "worker": (0.15, 0.10),
    "tower_crane": (0.65, 0.03),
    "hanging_hook": (0.15, 0.02),
    "vehicle_crane": (0.25, 0.06),
    "roller": (0.30, 0.01),
    "bulldozer": (0.30, 0.01),
    "excavator": (0.55, 0.03),
    "truck": (0.30, 0.04),
    "loader": (0.30, 0.02),
    "pump_truck": (0.20, 0.01),
    "concrete_mixer": (0.35, 0.02),
    "pile_driver": (0.10, 0.01),
    "other_vehicle": (0.50, 0.15),
}
# (истинный класс, за что его принимает детектор, вероятность)
CONFUSIONS = [
    ("vehicle_crane", "tower_crane", 0.20),
    ("tower_crane", "vehicle_crane", 0.05),
    ("pump_truck", "vehicle_crane", 0.30),
    ("concrete_mixer", "truck", 0.20),
]
# кратность техники на площадке, когда синтетика говорит «есть» (там только 0/1)
MULTIPLICITY = {
    "worker": (1, 15),
    "tower_crane": (1, 4),
    "other_vehicle": (1, 6),
    "truck": (1, 3),
}
MULTIPLICITY_DEFAULT = (1, 2)
CLASS_OUT_OF_VIEW_PROB = 0.15  # класс по проекту вообще не попадает в кадр
BAD_FRAME_PROB = 0.10  # ночь/туман/смаз: recall x0.3
BACKGROUND = {  # постоянные объекты вне стройки: (вероятность по проекту, макс. число)
    "tower_crane": (0.30, 3),
    "other_vehicle": (0.50, 8),
}
# редкость снимков: с этой вероятностью ежедневно, иначе раз в k дней (k равномерно)
DAILY_CADENCE_PROB = 0.35
CADENCE_RANGE = (2, 21)
MONITORING_FROM_START_PROB = 0.5  # иначе мониторинг начат в случайный день проекта


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)


# ---------------------------------------------------------------------------
# Истинное состояние площадки по дням (как build_project_timeline в v1)
# ---------------------------------------------------------------------------
def build_projects(activities, events, equipment_classes, phase_to_id):
    eq_idx = {c: i for i, c in enumerate(equipment_classes)}
    projects = {}
    for pid, plan in activities.groupby("project_id"):
        plan = plan.sort_values("activity_sequence")
        ev = events[events["project_id"] == pid].sort_values("at")
        start = ev["at"].min().floor("D")
        end = ev["at"].max().ceil("D")
        timestamps = pd.date_range(start + pd.Timedelta(hours=12), end, freq="24h")

        intervals, t = [], start
        for _, r in plan.iterrows():
            d = max(float(r["planned_duration_days"]), 1.0)
            intervals.append((t, t + pd.Timedelta(days=d), phase_to_id[r["phase"]]))
            t = t + pd.Timedelta(days=d)

        state = np.zeros(len(equipment_classes), dtype=np.int64)
        states, labels = [], []
        recs = ev[["equipment_class", "event", "at"]].to_dict("records")
        k = iv = 0
        for ts in timestamps:
            while k < len(recs) and recs[k]["at"] <= ts:
                i = eq_idx[recs[k]["equipment_class"]]
                state[i] = max(0, state[i] + (1 if recs[k]["event"] == "arrival" else -1))
                k += 1
            while iv < len(intervals) - 1 and ts >= intervals[iv][1]:
                iv += 1
            labels.append(intervals[iv][2])
            states.append(state.copy())
        projects[pid] = (np.stack(states), np.asarray(labels, dtype=np.int64))
    return projects


# ---------------------------------------------------------------------------
# Сэмпл: окно из WINDOW_SIZE дней, как его увидит прод
# ---------------------------------------------------------------------------
class Simulator:
    def __init__(self, equipment_classes):
        self.classes = equipment_classes
        self.E = len(equipment_classes)
        self.recall = np.array([DETECTOR[c][0] for c in equipment_classes])
        self.fp_rate = np.array([DETECTOR[c][1] for c in equipment_classes])
        idx = {c: i for i, c in enumerate(equipment_classes)}
        self.confusions = [(idx[a], idx[b], p) for a, b, p in CONFUSIONS]
        self.mult_range = [MULTIPLICITY.get(c, MULTIPLICITY_DEFAULT) for c in equipment_classes]
        self.background = [(idx[c], p, n) for c, (p, n) in BACKGROUND.items()]

    def project_profile(self, rng):
        """Свойства одной «камеры на одной стройке», постоянные во времени."""
        quality = rng.uniform(0.5, 1.4)
        recall = np.clip(self.recall * quality, 0.0, 0.95)
        recall[rng.random(self.E) < CLASS_OUT_OF_VIEW_PROB] = 0.0
        mult = np.array([rng.integers(lo, hi + 1) for lo, hi in self.mult_range])
        background = np.zeros(self.E, dtype=np.int64)
        for i, p, n in self.background:
            if rng.random() < p:
                background[i] = rng.integers(1, n + 1)
        return recall, mult, background

    def detect(self, rng, true_state, profile):
        """true_state: (D, E) истинные 0/1 из синтетики -> (D, E) счётчики «от YOLO»."""
        recall, mult, background = profile
        D = true_state.shape[0]
        units = true_state * mult + background
        day_recall = np.where(rng.random((D, 1)) < BAD_FRAME_PROB, recall * 0.3, recall)
        det = rng.binomial(units, np.broadcast_to(day_recall, units.shape))
        for a, b, p in self.confusions:
            moved = rng.binomial(det[:, a], p)
            det[:, a] -= moved
            det[:, b] += moved
        det += rng.poisson(self.fp_rate, size=det.shape)
        return det

    def sample(self, rng, state, labels):
        T = len(labels)
        W = WINDOW_SIZE
        t_end = int(rng.integers(0, T))
        # первый день мониторинга (первый снимок не раньше него)
        mon = int(rng.integers(0, 15)) if rng.random() < MONITORING_FROM_START_PROB else int(rng.integers(0, t_end + 1))
        mon = min(mon, t_end)
        if rng.random() < DAILY_CADENCE_PROB:
            obs_days = np.arange(mon, t_end + 1)
        else:
            k = rng.integers(*CADENCE_RANGE)
            obs_days = np.flatnonzero(rng.random(t_end + 1 - mon) < 1.0 / k) + mon
            if len(obs_days) == 0 or obs_days[-1] != t_end and rng.random() < 0.5:
                # в проде фазу чаще всего спрашивают сразу после загрузки снимка
                obs_days = np.append(obs_days, t_end)
        obs_days = np.unique(obs_days)

        w_start = t_end - W + 1  # может быть < 0: дни до начала проекта
        days = np.arange(w_start, t_end + 1)
        profile = self.project_profile(rng)

        # счётчики нужны в днях снимков внутри окна + последний снимок до окна (для forward-fill)
        before = obs_days[obs_days < w_start]
        inside = obs_days[obs_days >= w_start]
        need = np.concatenate([before[-1:], inside])
        det = self.detect(rng, state[need], profile) if len(need) else np.zeros((0, self.E))
        det_by_day = dict(zip(need.tolist(), det))

        counts_ff = np.zeros((W, self.E), dtype=np.float32)
        observed = np.zeros(W, dtype=np.float32)
        elapsed = np.full(W, -1.0, dtype=np.float32)
        first_obs = obs_days[0] if len(obs_days) else None
        last = det_by_day[int(before[-1])].astype(np.float32) if len(before) else np.zeros(self.E, np.float32)
        inside_set = set(inside.tolist())
        for j, d in enumerate(days):
            if d in inside_set:
                last = det_by_day[int(d)].astype(np.float32)
                observed[j] = 1.0
            if first_obs is not None and d >= first_obs:
                counts_ff[j] = last
                elapsed[j] = d - first_obs

        x = encode_v2(counts_ff, observed, elapsed)
        y = np.where(days >= 0, labels[np.clip(days, 0, T - 1)], -100)
        y = np.where(elapsed >= 0, y, -100)  # до первого снимка — нечего предсказывать
        return x, y.astype(np.int64)


def make_batch_set(sim, projects, ids, n, rng):
    xs, ys = [], []
    for _ in range(n):
        state, labels = projects[ids[rng.integers(0, len(ids))]]
        x, y = sim.sample(rng, state, labels)
        xs.append(x)
        ys.append(y)
    return torch.from_numpy(np.stack(xs)), torch.from_numpy(np.stack(ys))


def run(model, X, Y, text, structured, optimizer=None):
    train = optimizer is not None
    model.train(train)
    order = torch.randperm(len(X)) if train else torch.arange(len(X))
    tot_loss = tot_n = 0.0
    last_correct = last_n = all_correct = all_n = 0
    for i in range(0, len(X), BATCH_SIZE):
        b = order[i : i + BATCH_SIZE]
        x, y = X[b].to(DEVICE, non_blocking=True), Y[b].to(DEVICE, non_blocking=True)
        B = len(b)
        with torch.set_grad_enabled(train):
            out = model(x, text.expand(B, -1, -1), structured.expand(B, -1, -1))
            loss = F.cross_entropy(out.emissions.reshape(-1, out.emissions.size(-1)), y.reshape(-1), ignore_index=-100)
            if train:
                optimizer.zero_grad(set_to_none=True)
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                optimizer.step()
        valid = y != -100
        n_valid = int(valid.sum())
        tot_loss += loss.item() * n_valid
        tot_n += n_valid
        pred = out.emissions.argmax(-1)
        all_correct += int(((pred == y) & valid).sum())
        all_n += n_valid
        lv = valid[:, -1]
        last_correct += int(((pred[:, -1] == y[:, -1]) & lv).sum())
        last_n += int(lv.sum())
    return {
        "loss": tot_loss / max(tot_n, 1),
        "acc_all": all_correct / max(all_n, 1),
        "acc_last": last_correct / max(last_n, 1),  # то, что реально использует прод
    }


def main() -> None:
    set_seed(SEED)
    torch.set_num_threads(max(1, torch.get_num_threads()))
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    activities = pd.read_csv(DATA_DIR / "activities_with_equipment.csv")
    events = pd.read_csv(DATA_DIR / "equipment_events_with_context.csv")
    events["at"] = pd.to_datetime(events["at"])

    phase_names, equipment_classes, prevalence, phase_meta = build_phase_meta(DATA_DIR)
    phase_to_id = {p: i for i, p in enumerate(phase_names)}

    from sentence_transformers import SentenceTransformer  # noqa: PLC0415

    encoder = SentenceTransformer(TEXT_MODEL_NAME, device="cpu")
    with torch.no_grad():
        text = encoder.encode(
            phase_meta["phase_text"].tolist(), convert_to_tensor=True, normalize_embeddings=True
        ).float().unsqueeze(0)
    structured = torch.from_numpy(build_phase_structured(phase_names, prevalence, phase_meta)).unsqueeze(0)

    t0 = time.time()
    projects = build_projects(activities, events, equipment_classes, phase_to_id)
    split = activities[["project_id", "split"]].drop_duplicates().set_index("project_id")["split"]
    ids = {s: [p for p in projects if split[p] == s] for s in ("train", "validation", "test")}
    print(f"проекты: { {k: len(v) for k, v in ids.items()} }, построено за {time.time() - t0:.0f}s", flush=True)

    sim = Simulator(equipment_classes)
    X_val, Y_val = make_batch_set(sim, projects, ids["validation"], EVAL_SAMPLES, np.random.default_rng(1))
    X_test, Y_test = make_batch_set(sim, projects, ids["test"], EVAL_SAMPLES, np.random.default_rng(2))

    obs_dim = observation_dim(FEATURE_VERSION, len(equipment_classes))
    model = ConstructionPhaseModel(
        observation_dim=obs_dim,
        phase_text_dim=text.shape[-1],
        phase_structured_dim=structured.shape[-1],
        max_len=WINDOW_SIZE,
        causal=True,
    )
    model.to(DEVICE)
    text, structured = text.to(DEVICE), structured.to(DEVICE)
    optimizer = torch.optim.AdamW(model.parameters(), lr=LEARNING_RATE, weight_decay=WEIGHT_DECAY)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=EPOCHS)
    start_epoch, best = 1, math.inf
    if RESUME_FROM:
        resume = torch.load(RESUME_FROM, map_location="cpu")
        model.load_state_dict(resume["model_state_dict"])
        if "optimizer_state_dict" in resume:
            optimizer.load_state_dict(resume["optimizer_state_dict"])
        for _ in range(resume["epoch"]):
            scheduler.step()
        start_epoch, best = resume["epoch"] + 1, resume["val_metrics"]["loss"]
        print(f"продолжаю с эпохи {start_epoch} ({RESUME_FROM}), lr {scheduler.get_last_lr()[0]:.2e}", flush=True)
    print(f"устройство: {DEVICE}", flush=True)

    config = {
        "window_size": WINDOW_SIZE,
        "observation_dim": obs_dim,
        "phase_text_dim": text.shape[-1],
        "phase_structured_dim": structured.shape[-1],
        "feature_version": FEATURE_VERSION,
        "causal": True,
    }
    train_rng = np.random.default_rng(SEED + start_epoch)
    for epoch in range(start_epoch, EPOCHS + 1):
        t0 = time.time()
        # новый шум детектора/редкость снимков каждую эпоху — это и есть аугментация
        X, Y = make_batch_set(sim, projects, ids["train"], TRAIN_SAMPLES_PER_EPOCH, train_rng)
        tr = run(model, X, Y, text, structured, optimizer)
        with torch.no_grad():
            va = run(model, X_val, Y_val, text, structured)
        scheduler.step()
        mark = ""
        if va["loss"] < best:
            best = va["loss"]
            torch.save(
                {
                    "epoch": epoch,
                    "model_state_dict": model.state_dict(),
                    "optimizer_state_dict": optimizer.state_dict(),
                    "val_metrics": va,
                    "phase_names": phase_names,
                    "equipment_classes": equipment_classes,
                    "config": config,
                    "detector_simulation": {
                        "detector": DETECTOR, "confusions": CONFUSIONS, "multiplicity": MULTIPLICITY,
                        "background": BACKGROUND, "class_out_of_view_prob": CLASS_OUT_OF_VIEW_PROB,
                        "bad_frame_prob": BAD_FRAME_PROB, "daily_cadence_prob": DAILY_CADENCE_PROB,
                        "cadence_range": CADENCE_RANGE, "monitoring_from_start_prob": MONITORING_FROM_START_PROB,
                    },
                },
                OUT_DIR / "best.pt",
            )
            mark = "  <-- best"
        print(
            f"epoch {epoch:02d} | train loss {tr['loss']:.3f} acc_last {tr['acc_last']:.3f} | "
            f"val loss {va['loss']:.3f} acc_last {va['acc_last']:.3f} acc_all {va['acc_all']:.3f} | "
            f"{time.time() - t0:.0f}s{mark}",
            flush=True,
        )

    ckpt = torch.load(OUT_DIR / "best.pt", map_location="cpu")
    model.load_state_dict(ckpt["model_state_dict"])
    with torch.no_grad():
        te = run(model, X_test, Y_test, text, structured)
    ckpt["test_metrics"] = te
    torch.save(ckpt, OUT_DIR / "best.pt")
    print(f"test (синтетика + симулятор детектора, отложенные проекты): {te}")
    print(f"сохранено: {OUT_DIR / 'best.pt'} (эпоха {ckpt['epoch']})")


if __name__ == "__main__":
    main()
