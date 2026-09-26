"""Итоговая фаза записи журнала — ансамбль сигналов.

1. services/phase — фаза по технике (сам сервис — ансамбль двух моделей
   3:1, см. его worker.py), полный вектор вероятностей в PhaseResult.probs;
2. services/visual_phase — фаза по самому снимку (сам сервис — ансамбль
   DINOv2-головы, SigLIP-головы и SigLIP zero-shot поровну),
   VisualPhaseResult.probs.

Как выбраны веса — phase_determination/eval_all.py: 60 размеченных
таймлапс-строек, held-out по стройкам (10 фолдов), прод-детектор. Только
техника 52.3%, прежняя схема (техника + одна DINOv2-голова) 60.6%, эта
66.4%; лучший вариант по сетке весов почти равномерный (66.5% против 66.4%
у равных весов), так что переобучения весов практически нет. Для сравнения,
прежний прод (обученный на 14 стройках) на 46 новых стройках — 53.2%
(phase_determination/eval_prod_on_new.py). Эталон размечен вручную по
контактным листам, длительности строек оценены — цифры ориентировочные.
Сильные фазы — земляные работы, каркас, отделка (67-80% recall); кладка,
MEP, сдача и подготовка площадки почти не распознаются (мало примеров).

Сознательно НЕ подключён фильтр по истории проекта (forward-фильтр с
монотонными переходами фаз, +1 п.п. в ensemble_eval.py): там шаг фильтра —
каждый день с кадром таймлапса, а в проде шаг — запись журнала (раз в
неделю и реже). С тем же «фаза держится с вероятностью 0.9» на запись он
перестаёт верить первой записи с новой фазой — детектор уверенно видит
фундамент, фильтр держит земляные работы до следующих записей (ловит
tests/test_progress.py::test_delay_command_carries_observed_phase_start).
История и так учтена внутри модели по технике (128-дневное окно).

Всё здесь — чистые функции над dict[фаза -> вероятность], без БД и брокера;
оркестрация (дождаться обоих результатов, записать, запустить delay) — в
analysis.py (_maybe_finalize_phase).
"""
from __future__ import annotations

import math

from integrations.schemas import CANONICAL_PHASES

# log p = 0.4 log p_техника + 0.6 log p_визуально. С учётом смешивания внутри
# сервисов это 0.3 v3 / 0.1 v2 / 0.2 DINOv2 / 0.2 SigLIP / 0.2 SigLIP zero-shot.
EQUIPMENT_WEIGHT = 0.4
VISUAL_WEIGHT = 0.6
_EPS = 1e-6

Distribution = dict[str, float]


def _normalize(values: list[float]) -> Distribution:
    total = sum(values)
    if total <= 0 or not math.isfinite(total):
        return {p: 1.0 / len(CANONICAL_PHASES) for p in CANONICAL_PHASES}
    return {p: v / total for p, v in zip(CANONICAL_PHASES, values)}


def from_point_estimate(phase_name: str | None, confidence: float | None) -> Distribution | None:
    """Для воркеров без probs (старые версии сервисов): вся уверенность —
    названной фазе, остаток поровну остальным."""
    if phase_name not in CANONICAL_PHASES:
        return None
    c = min(max(confidence if confidence is not None else 0.5, _EPS), 1 - _EPS)
    rest = (1 - c) / (len(CANONICAL_PHASES) - 1)
    return {p: (c if p == phase_name else rest) for p in CANONICAL_PHASES}


def _complete(dist: Distribution) -> Distribution:
    """Фазы, которых классификатор не знает (visual_phase не видит
    Preconstruction), — нейтральны: среднее по известным, а не ноль. Иначе
    визуальный сигнал «запрещал» бы фазу, про которую ему нечего сказать."""
    known = {p: v for p, v in dist.items() if p in CANONICAL_PHASES}
    neutral = sum(known.values()) / len(known) if known else 1.0
    return _normalize([known.get(p, neutral) for p in CANONICAL_PHASES])


def fuse(equipment: Distribution | None, visual: Distribution | None) -> Distribution | None:
    """Log-линейное объединение; отсутствующий сигнал просто не участвует."""
    parts = [(EQUIPMENT_WEIGHT, equipment), (VISUAL_WEIGHT, visual)]
    parts = [(w, _complete(d)) for w, d in parts if d]
    if not parts:
        return None
    total_w = sum(w for w, _ in parts)
    logs = [sum(w / total_w * math.log(d[p] + _EPS) for w, d in parts) for p in CANONICAL_PHASES]
    top = max(logs)
    return _normalize([math.exp(v - top) for v in logs])


def top(dist: Distribution) -> tuple[str, float]:
    phase = max(dist, key=dist.__getitem__)
    return phase, dist[phase]
