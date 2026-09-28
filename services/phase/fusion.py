"""Объединение двух сигналов о фазе по истории проекта — чистый numpy.

Сигналы по дням окна (WINDOW_SIZE календарных дней, заканчивается as_of_date):
  * техника   — log-вероятности ансамбля моделей по технике (worker.
                predict_probs: последняя позиция окна, заканчивающегося этим
                днём — как в обучении) на днях, когда площадку реально снимали;
                на днях, заполненных forward-fill, вес FusionWeights.filled
                (по умолчанию 0: повторять одну и ту же съёмку много раз =
                переоценивать её);
  * визуальный — вероятности фаз от services/visual_phase (ансамбль SigLIP +
                концепты) на днях журнальных записей.

Модель — скрытая марковская цепь по фазам в каноническом порядке. Шаг цепи —
не календарный день, а переход от одного дня с наблюдениями к следующему:
  * вероятность сдвинуться вперёд за шаг растёт с промежутком между съёмками
    (1 - (1 - 1/длительность)^дней), но не больше MAX_ADVANCE_PER_STEP —
    переход в новую фазу должен подтвердиться наблюдением;
  * вперёд чаще всего на следующую фазу, реже через одну-две;
  * назад — очень маловероятно, но не запрещено (жёсткий запрет навсегда
    закрепил бы раннюю ошибку);
  * фазы, которых нет в плане, достижимы, но с понижающим множителем.

Из плана берутся ТОЛЬКО порядок и длительности, не даты: иначе оценка фазы
подстраивалась бы под график, и отставание стало бы невидимым.

Итог — прямой проход (forward filtering): P(фаза | все наблюдения до
as_of_date включительно). После последнего наблюдения фаза НЕ сдвигается:
продвигать её «по плановой скорости» без съёмки значило бы молча допускать,
что стройка идёт по графику, и прятать отставание. Вместо этого в ответе есть
days_since_last_observation — насколько устарела оценка.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

DEFAULT_PHASE_DAYS = 60.0
MIN_PHASE_DAYS, MAX_PHASE_DAYS = 7.0, 400.0
FORWARD_SKIP_WEIGHTS = (1.0, 0.25, 0.05)   # на +1, +2, +3 фазы вперёд
MAX_ADVANCE_PER_STEP = 0.5                 # максимум сдвига вперёд между двумя наблюдениями
BACKWARD_PROB = 0.01                       # суммарно на все переходы назад за шаг
NOT_IN_PLAN_FACTOR = 0.1                   # множитель входа в фазу, которой нет в плане
VISUAL_FLOOR = 1e-3                        # визуальные вероятности не ниже этого


@dataclass
class FusionWeights:
    # Осторожное допущение, не результат подбора: объединение по истории
    # ещё не проверено ни на одном реальном проекте с серией записей.
    equipment: float = 0.5
    visual: float = 1.0
    filled: float = 0.0      # вес дней, заполненных forward-fill


def _logsumexp(x: np.ndarray, axis: int) -> np.ndarray:
    m = x.max(axis=axis, keepdims=True)
    m = np.where(np.isfinite(m), m, 0.0)
    return (m + np.log(np.exp(x - m).sum(axis=axis, keepdims=True))).squeeze(axis)


def plan_mask_and_durations(
    phase_names: list[str], plan: list[tuple[str, float]]
) -> tuple[np.ndarray, np.ndarray]:
    """plan — [(фаза, плановая длительность, дн.)]. Пустой план = все фазы
    разрешены, длительности по умолчанию."""
    n = len(phase_names)
    idx = {p.lower(): i for i, p in enumerate(phase_names)}
    durations = np.full(n, DEFAULT_PHASE_DAYS)
    if not plan:
        return np.ones(n, dtype=bool), durations
    in_plan = np.zeros(n, dtype=bool)
    for name, days in plan:
        i = idx.get(name.strip().lower())
        if i is None:
            continue
        in_plan[i] = True
        if days and days > 0:
            durations[i] = days
    if not in_plan.any():                 # план не распознан — не ограничиваем
        in_plan[:] = True
    return in_plan, np.clip(durations, MIN_PHASE_DAYS, MAX_PHASE_DAYS)


def transition_matrix(in_plan: np.ndarray, durations: np.ndarray, gap_days: float = 1.0) -> np.ndarray:
    """T[i, j] = P(фаза на следующем наблюдении = j | на текущем = i), если
    между наблюдениями gap_days дней."""
    n = len(durations)
    plan_factor = np.where(in_plan, 1.0, NOT_IN_PLAN_FACTOR)
    T = np.zeros((n, n))
    for i in range(n):
        p_adv = 1.0 - (1.0 - 1.0 / durations[i]) ** max(gap_days, 0.0) if i < n - 1 else 0.0
        p_adv = min(p_adv, MAX_ADVANCE_PER_STEP)
        fw = np.zeros(n)
        for k, w in enumerate(FORWARD_SKIP_WEIGHTS, start=1):
            if i + k < n:
                fw[i + k] = w * plan_factor[i + k]
        if fw.sum() > 0:
            T[i] += p_adv * fw / fw.sum()
        else:
            p_adv = 0.0
        p_back = BACKWARD_PROB if i > 0 and gap_days > 0 else 0.0
        if p_back:
            bw = plan_factor[:i] / plan_factor[:i].sum()
            T[i, :i] += p_back * bw
        T[i, i] += 1.0 - p_adv - p_back
    return T


def forward_filter(
    log_lik: np.ndarray, day_index: np.ndarray, in_plan: np.ndarray, durations: np.ndarray, prior: np.ndarray
) -> np.ndarray:
    """log_lik — (наблюдения, фазы) только по дням с наблюдениями, day_index —
    номера этих дней. Возвращает апостериорные вероятности на каждое наблюдение."""
    log_alpha = np.log(prior) + log_lik[0]
    log_alpha -= _logsumexp(log_alpha, 0)
    out = [np.exp(log_alpha)]
    for t in range(1, len(log_lik)):
        T = transition_matrix(in_plan, durations, float(day_index[t] - day_index[t - 1]))
        logT = np.log(np.maximum(T, 1e-300))
        log_alpha = _logsumexp(log_alpha[:, None] + logT, 0) + log_lik[t]
        log_alpha -= _logsumexp(log_alpha, 0)
        out.append(np.exp(log_alpha))
    return np.stack(out)


def fuse(
    phase_names: list[str],
    equipment_logprobs: np.ndarray,       # (дни, фазы) log_softmax эмиссий
    day_kind: np.ndarray,                 # (дни,) "real" | "filled" | "none"
    visual_probs: np.ndarray,             # (дни, фазы), NaN там, где снимков нет
    plan: list[tuple[str, float]],
    weights: FusionWeights = FusionWeights(),
) -> dict:
    n_days, n = equipment_logprobs.shape
    w_eq = np.select([day_kind == "real", day_kind == "filled"], [weights.equipment, weights.equipment * weights.filled], 0.0)
    has_visual = ~np.isnan(visual_probs).any(axis=1)

    log_lik = w_eq[:, None] * equipment_logprobs
    vis = np.where(has_visual[:, None], visual_probs, 1.0)
    vis = np.maximum(vis, VISUAL_FLOOR)
    vis = vis / vis.sum(axis=1, keepdims=True)
    log_lik = log_lik + np.where(has_visual[:, None], weights.visual * np.log(vis), 0.0)

    evidence = (w_eq > 0) | has_visual
    if not evidence.any():
        # Нет ни съёмки, ни снимков в окне — как раньше, берём последнюю эмиссию модели.
        evidence[-1] = True
        log_lik[-1] = equipment_logprobs[-1]
    day_index = np.where(evidence)[0]

    in_plan, durations = plan_mask_and_durations(phase_names, plan)
    prior = np.where(in_plan, 1.0, NOT_IN_PLAN_FACTOR)
    prior = prior / prior.sum()
    posterior = forward_filter(log_lik[day_index], day_index, in_plan, durations, prior)

    p = posterior[-1]
    best = int(p.argmax())
    real_days = np.where(day_kind == "real")[0]
    vis_days = np.where(has_visual)[0]
    equipment_phase = phase_names[int(equipment_logprobs[real_days[-1]].argmax())] if len(real_days) else None
    visual_phase = phase_names[int(visual_probs[vis_days[-1]].argmax())] if len(vis_days) else None
    return {
        "phase": phase_names[best],
        "confidence": float(p[best]),
        "phase_probs": {ph: round(float(v), 4) for ph, v in zip(phase_names, p)},
        "top_phases": [{"phase": phase_names[i], "prob": round(float(p[i]), 4)} for i in np.argsort(-p)[:3]],
        "equipment_phase": equipment_phase,
        "visual_phase": visual_phase,
        "equipment_days": int(len(real_days)),
        "visual_days": int(len(vis_days)),
        "observations_used": int(len(day_index)),
        "days_since_last_observation": int(n_days - 1 - day_index[-1]),
    }
