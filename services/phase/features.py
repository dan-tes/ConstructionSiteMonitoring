"""Признаки модуля 3 — ОДИН код для прода (worker.py) и для обучения
(phase_determination/train_phase_v2.py), чтобы окно, которое видит модель
в проде, строилось ровно так же, как при обучении.

Почему это вынесено: у первой модели обучение и прод расходились —
в ноутбуке (construction_phase_training.ipynb, ячейка 12) счётчик техники был
ИСТИННЫМ (шум детектора менял только флаги visible/confidence), а в проде
счётчик приходит от YOLO с recall ~50-60% на широких ракурсах, при этом
visible/confidence прод всегда ставил в 1. Модель ни разу не видела данных,
похожих на прод. См. phase_determination/timelapse_phase_eval.py (бенчмарк).

Версии признаков (checkpoint["config"]["feature_version"]):
  1 — исходная: [count/5 (13), visible (13), confidence (13)], dim 39.
      Оставлена, чтобы старый чекпоинт работал и был базой для сравнения.
  2 — [log-count (13), observed_today, monitoring_started, elapsed], dim 16:
      - log-count: log1p(min(n, 10)) / log1p(10) — на реальных кадрах бывает
        и 4-5 кранов, clip до 5 как в v1 это съедал;
      - observed_today: 1 — в этот день был реальный снимок, 0 — значение
        протянуто forward-fill'ом с прошлого снимка (раньше модель не могла
        отличить свежее наблюдение от протянутого на месяц);
      - monitoring_started: 0 — дни окна до первого снимка проекта (там нули
        и это «нет данных», а не «нет техники»);
      - elapsed: дней с первого снимка проекта / 1000 (клип 1.5). Единственный
        признак, который отличает поздний каркас от отделки, когда на
        площадке месяцами одно и то же (башенный кран + рабочие).
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

FEATURE_VERSION_LEGACY = 1
FEATURE_VERSION = 2
LOG_COUNT_CAP = 10
ELAPSED_SCALE_DAYS = 1000.0
ELAPSED_CLIP = 1.5


def observation_dim(feature_version: int, num_equipment: int) -> int:
    return num_equipment * 3 if feature_version == FEATURE_VERSION_LEGACY else num_equipment + 3


# ---------------------------------------------------------------------------
# Метаданные фаз (текст + структурные признаки) — перенесено из worker.py
# без изменений; почему prevalence, а не бинарный набор классов, — см.
# phase_determination/retrain_weighted_equipment.py.
# ---------------------------------------------------------------------------
def build_phase_meta(data_dir: Path) -> tuple[list[str], list[str], dict[str, np.ndarray], pd.DataFrame]:
    activities = pd.read_csv(data_dir / "activities_with_equipment.csv")
    equipment_desc = pd.read_csv(data_dir / "equipment_descriptions.csv")
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


def build_phase_structured(
    phase_names: list[str], phase_equipment_prevalence: dict[str, np.ndarray], phase_meta: pd.DataFrame
) -> np.ndarray:
    duration = phase_meta["duration_days"].to_numpy(dtype=np.float32)
    duration = duration / max(duration.max(), 1.0)
    criticality = phase_meta["criticality"].to_numpy(dtype=np.float32)
    return np.stack(
        [
            np.concatenate(
                [phase_equipment_prevalence[p], np.array([duration[i], criticality[i]], dtype=np.float32)]
            )
            for i, p in enumerate(phase_names)
        ]
    )  # (NUM_PHASES, NUM_EQUIPMENT + 2)


# ---------------------------------------------------------------------------
# Окно наблюдений
# ---------------------------------------------------------------------------
def encode_v2(counts_ff: np.ndarray, observed: np.ndarray, elapsed_days: np.ndarray) -> np.ndarray:
    """counts_ff: (W, E) сырые счётчики, уже протянутые forward-fill'ом
    (нули до первого снимка); observed: (W,) 0/1 — был ли снимок в этот день;
    elapsed_days: (W,) дней с первого снимка проекта, < 0 — снимков ещё не
    было. Векторизовано: обучение вызывает это на каждом сэмпле."""
    log_counts = np.log1p(np.minimum(counts_ff, LOG_COUNT_CAP)) / np.log1p(LOG_COUNT_CAP)
    started = (elapsed_days >= 0).astype(np.float32)
    elapsed = np.clip(np.maximum(elapsed_days, 0) / ELAPSED_SCALE_DAYS, 0.0, ELAPSED_CLIP) * started
    return np.concatenate(
        [log_counts, observed[:, None], started[:, None], elapsed[:, None]], axis=1
    ).astype(np.float32)


def dense_history(
    history: dict[date, dict[str, int]], as_of_date: date, window_size: int, equipment_classes: list[str]
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Разреженная история проекта (день -> счётчики по классам) -> три
    плотных массива для окна из `window_size` дней, заканчивающегося
    `as_of_date`: forward-fill счётчиков, флаг «снимок в этот день» и дни с
    первого снимка проекта (он может быть и раньше начала окна)."""
    idx = {c: i for i, c in enumerate(equipment_classes)}
    days = sorted(d for d in history if d <= as_of_date)
    first = days[0] if days else None
    window_start = as_of_date - timedelta(days=window_size - 1)

    counts_ff = np.zeros((window_size, len(equipment_classes)), dtype=np.float32)
    observed = np.zeros(window_size, dtype=np.float32)
    elapsed = np.full(window_size, -1.0, dtype=np.float32)

    last = np.zeros(len(equipment_classes), dtype=np.float32)
    k = 0
    for t in range(window_size):
        day = window_start + timedelta(days=t)
        while k < len(days) and days[k] <= day:
            last = np.zeros(len(equipment_classes), dtype=np.float32)
            for cls, n in history[days[k]].items():
                if cls in idx:
                    last[idx[cls]] = float(n)
            if days[k] == day:
                observed[t] = 1.0
            k += 1
        counts_ff[t] = last
        if first is not None and day >= first:
            elapsed[t] = (day - first).days
    return counts_ff, observed, elapsed


def build_window_legacy(
    history: dict[date, dict[str, int]], as_of_date: date, window_size: int, equipment_classes: list[str]
) -> np.ndarray:
    """v1 — ровно то, что прод делал до v2 (forward-fill, visible и
    confidence = 1 во всех днях). Нужен только для старого чекпоинта."""
    counts_ff, _, _ = dense_history(history, as_of_date, window_size, equipment_classes)
    count_feature = np.clip(counts_ff, 0, 5) / 5.0
    ones = np.ones_like(count_feature)
    return np.concatenate([count_feature, ones, ones], axis=1).astype(np.float32)


def build_window(
    history: dict[date, dict[str, int]],
    as_of_date: date,
    window_size: int,
    equipment_classes: list[str],
    feature_version: int,
) -> np.ndarray:
    if feature_version == FEATURE_VERSION_LEGACY:
        return build_window_legacy(history, as_of_date, window_size, equipment_classes)
    return encode_v2(*dense_history(history, as_of_date, window_size, equipment_classes))
