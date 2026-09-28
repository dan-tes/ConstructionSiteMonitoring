"""Сценарии объединения техники и изображения (fusion.py) — синтетические
наблюдения, без моделей: `python -m pytest test_fusion.py`."""

import numpy as np

from fusion import FusionWeights, fuse

PHASES = [
    "Preconstruction",
    "Site Preparation",
    "Earthwork",
    "Foundation",
    "Structural Frame",
    "Masonry",
    "MEP",
    "Finishing",
    "External Works",
    "Commissioning",
]
PLAN = [(p, 30.0) for p in PHASES]
W = 128


def _dist(**weights: float) -> np.ndarray:
    named = {k.replace("_", " "): v for k, v in weights.items()}
    d = np.array([named.get(p, 0.01) for p in PHASES])
    return d / d.sum()


class Timeline:
    def __init__(self) -> None:
        self.eq = np.full((W, len(PHASES)), np.log(1 / len(PHASES)))
        self.kind = np.array(["none"] * W, dtype=object)
        self.vis = np.full((W, len(PHASES)), np.nan)

    def equipment(self, day: int, dist: np.ndarray) -> "Timeline":
        self.eq[day] = np.log(dist)
        self.kind[day] = "real"
        self.kind[day + 1 :][self.kind[day + 1 :] == "none"] = "filled"
        return self

    def visual(self, day: int, dist: np.ndarray) -> "Timeline":
        self.vis[day] = dist
        return self

    def fuse(self, weights: FusionWeights = FusionWeights()) -> dict:
        return fuse(PHASES, self.eq, self.kind, self.vis, PLAN, weights)


def test_rebar_photo_beats_a_lingering_excavator():
    earthwork = _dist(Earthwork=0.7, Foundation=0.2)
    t = Timeline()
    for day in (60, 80, 100):
        t.equipment(day, earthwork)
    t.equipment(W - 1, earthwork)
    assert t.fuse()["phase"] == "Earthwork"
    t.visual(W - 1, _dist(Foundation=0.8, Earthwork=0.1))
    assert t.fuse()["phase"] == "Foundation"


def test_normal_progress_reaches_masonry():
    t = Timeline()
    for day, phase in [(10, "Earthwork"), (35, "Foundation"), (60, "Structural_Frame"), (90, "Masonry"), (W - 1, "Masonry")]:
        t.visual(day, _dist(**{phase: 0.8}))
    out = t.fuse()
    assert out["phase"] == "Masonry" and out["confidence"] > 0.9


def test_a_single_backward_outlier_does_not_erase_history():
    t = Timeline()
    for day in (40, 60, 80):
        t.visual(day, _dist(Structural_Frame=0.8))
    t.visual(W - 1, _dist(Earthwork=0.8))
    assert t.fuse()["phase"] == "Structural Frame"


def test_a_confirmed_rollback_is_accepted():
    t = Timeline()
    for day in (20, 30, 40):
        t.visual(day, _dist(Structural_Frame=0.8))
    for day in range(60, W, 10):
        t.visual(day, _dist(Earthwork=0.8))
    assert t.fuse()["phase"] == "Earthwork"


def test_phase_is_not_advanced_past_the_last_observation():
    t = Timeline().visual(20, _dist(Foundation=0.8))
    out = t.fuse()
    assert out["phase"] == "Foundation"
    assert out["days_since_last_observation"] == W - 1 - 20


def test_forward_filled_days_carry_no_weight_by_default():
    t = Timeline().equipment(20, _dist(Earthwork=0.9))
    assert t.fuse()["observations_used"] == 1
    assert t.fuse(FusionWeights(filled=0.1))["observations_used"] == W - 20


def test_first_observation_of_the_next_phase_is_believed():
    """Шаг — запись журнала, а не день: одно уверенное наблюдение следующей
    фазы должно переключать ответ сразу (иначе прогноз задержки видит смену
    фазы на запись позже)."""
    t = Timeline()
    for day in (40, 60, 80):
        t.visual(day, _dist(Earthwork=0.8))
    t.visual(W - 1, _dist(Foundation=0.8))
    assert t.fuse()["phase"] == "Foundation"


def test_no_observations_falls_back_to_the_latest_emission():
    t = Timeline()
    t.eq[-1] = np.log(_dist(Finishing=0.9))
    out = t.fuse()
    assert out["phase"] == "Finishing" and out["equipment_phase"] is None


def test_each_signal_is_reported_separately():
    t = Timeline().equipment(W - 1, _dist(Earthwork=0.7)).visual(W - 1, _dist(Foundation=0.8))
    out = t.fuse()
    assert (out["equipment_phase"], out["visual_phase"]) == ("Earthwork", "Foundation")
    assert (out["equipment_days"], out["visual_days"]) == (1, 1)
    assert abs(sum(out["phase_probs"].values()) - 1) < 1e-3
