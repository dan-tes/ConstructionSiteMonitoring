"""Unit tests for the delay-forecast module (services/delay/worker.py).

Pure-function tests only — no RabbitMQ connection is exercised (that's
main()'s job, not forecast_delay()/compute()'s).
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

import worker
from schemas import DelayForecastCommand, PlanPhaseIn
from worker import compute, forecast_delay

PLANNED_START = date(2026, 1, 1)


def _stage(phase: str, order: int, duration: float) -> PlanPhaseIn:
    return PlanPhaseIn(phase=phase, phase_order=order, planned_duration_days=duration)


class TestBaselineNotebookParity:
    """worker.forecast_delay() is documented (see its module docstring) as
    copied verbatim from latency_prediction/delay_forecasting_baseline.ipynb.
    These lock in exact numeric parity with that notebook's own named test
    scenarios (its cell 5), so a future edit to the math can't silently
    diverge from the reference model without a test failure."""

    PLANNED_DURATION_DAYS = 200
    PHASE_START_OFFSET_DAYS = 60
    PHASE_PLANNED_DURATION_DAYS = 40

    def _forecast(self, **overrides):
        kwargs = dict(
            planned_start=PLANNED_START,
            planned_duration_days=self.PLANNED_DURATION_DAYS,
            phase_start_offset_days=self.PHASE_START_OFFSET_DAYS,
            phase_planned_duration_days=self.PHASE_PLANNED_DURATION_DAYS,
        )
        kwargs.update(overrides)
        return forecast_delay(**kwargs)

    def test_on_track_matches_notebook(self):
        result = self._forecast(
            current_phase_started_at=date(2026, 3, 1),
            status_date=date(2026, 3, 20),
            phase_confidence=1.0,
        )
        assert result.status == "on_track"
        assert result.forecast_delay_days == pytest.approx(-2.5316455696202524)
        assert result.effective_spi_time == pytest.approx(1.0128205128205128)

    def test_warning_delay_matches_notebook(self):
        result = self._forecast(
            current_phase_started_at=date(2026, 3, 1),
            status_date=date(2026, 4, 15),
            phase_confidence=1.0,
        )
        assert result.status == "warning_delay"
        assert result.forecast_delay_days == pytest.approx(8.0)
        assert result.effective_spi_time == pytest.approx(0.9615384615384616)

    def test_critical_delay_matches_notebook(self):
        result = self._forecast(
            current_phase_started_at=date(2026, 3, 1),
            status_date=date(2026, 5, 20),
            phase_confidence=1.0,
        )
        assert result.status == "critical_delay"
        assert result.forecast_delay_days == pytest.approx(78.0)
        assert result.effective_spi_time == pytest.approx(0.7194244604316546)

    def test_ahead_matches_notebook(self):
        result = self._forecast(
            current_phase_started_at=date(2026, 2, 20),
            status_date=date(2026, 3, 5),
            phase_confidence=1.0,
        )
        assert result.status == "ahead"
        assert result.forecast_delay_days == pytest.approx(-27.39726027397262)
        assert result.effective_spi_time == pytest.approx(1.1587301587301588)

    def test_low_confidence_shrinkage_matches_notebook(self):
        # Same instant as test_warning_delay_matches_notebook, but with
        # phase_confidence=0.3 — the forecast should shrink toward neutral
        # (SPI=1) rather than stay at the full warning-level delay.
        result = self._forecast(
            current_phase_started_at=date(2026, 3, 1),
            status_date=date(2026, 4, 15),
            phase_confidence=0.3,
        )
        assert result.status == "on_track"
        assert result.forecast_delay_days == pytest.approx(2.3346303501945442)
        assert result.effective_spi_time == pytest.approx(0.9884615384615385)


class TestForecastDelay:
    def test_status_date_before_planned_start_is_not_started(self):
        result = forecast_delay(
            planned_start=PLANNED_START,
            planned_duration_days=30,
            phase_start_offset_days=0,
            phase_planned_duration_days=10,
            current_phase_started_at=PLANNED_START,
            status_date=PLANNED_START - timedelta(days=1),
        )
        assert result.status == "not_started"
        assert result.forecast_delay_days is None

    def test_zero_elapsed_time_is_insufficient_data(self):
        result = forecast_delay(
            planned_start=PLANNED_START,
            planned_duration_days=30,
            phase_start_offset_days=0,
            phase_planned_duration_days=10,
            current_phase_started_at=PLANNED_START,
            status_date=PLANNED_START,
        )
        assert result.status == "insufficient_data"

    def test_zero_phase_duration_is_insufficient_data(self):
        result = forecast_delay(
            planned_start=PLANNED_START,
            planned_duration_days=30,
            phase_start_offset_days=0,
            phase_planned_duration_days=0,
            current_phase_started_at=PLANNED_START,
            status_date=PLANNED_START + timedelta(days=5),
        )
        assert result.status == "insufficient_data"

    def test_on_track_when_progress_matches_elapsed_time(self):
        result = forecast_delay(
            planned_start=PLANNED_START,
            planned_duration_days=30,
            phase_start_offset_days=10,
            phase_planned_duration_days=10,
            current_phase_started_at=PLANNED_START + timedelta(days=10),
            status_date=PLANNED_START + timedelta(days=15),
        )
        assert result.status == "on_track"
        assert result.forecast_delay_days == pytest.approx(0.0)
        assert result.effective_spi_time == pytest.approx(1.0)

    def test_critical_delay_when_phase_capped_at_full_progress_falls_behind(self):
        # Phase finishes (progress caps at 1.0) long before status_date, so
        # earned schedule stalls while actual elapsed time keeps growing.
        result = forecast_delay(
            planned_start=PLANNED_START,
            planned_duration_days=30,
            phase_start_offset_days=10,
            phase_planned_duration_days=10,
            current_phase_started_at=PLANNED_START + timedelta(days=10),
            status_date=PLANNED_START + timedelta(days=40),
            warning_threshold_days=5,
            critical_threshold_days=20,
        )
        assert result.status == "critical_delay"
        assert result.forecast_delay_days == pytest.approx(30.0)

    def test_warning_delay_between_thresholds(self):
        result = forecast_delay(
            planned_start=PLANNED_START,
            planned_duration_days=30,
            phase_start_offset_days=10,
            phase_planned_duration_days=10,
            current_phase_started_at=PLANNED_START + timedelta(days=10),
            status_date=PLANNED_START + timedelta(days=25),
            warning_threshold_days=5,
            critical_threshold_days=100,
        )
        assert result.status == "warning_delay"
        assert result.forecast_delay_days == pytest.approx(7.5)

    def test_ahead_when_phase_started_earlier_than_scheduled(self):
        result = forecast_delay(
            planned_start=PLANNED_START,
            planned_duration_days=30,
            phase_start_offset_days=10,
            phase_planned_duration_days=10,
            current_phase_started_at=PLANNED_START + timedelta(days=5),
            status_date=PLANNED_START + timedelta(days=12),
        )
        assert result.status == "ahead"
        assert result.forecast_delay_days < -7.0

    def test_zero_confidence_neutralizes_spi_to_on_track(self):
        # phase_confidence=0 must fully discount the phase detector's read,
        # regardless of how far behind the raw SPI(t) would otherwise land.
        result = forecast_delay(
            planned_start=PLANNED_START,
            planned_duration_days=30,
            phase_start_offset_days=0,
            phase_planned_duration_days=10,
            current_phase_started_at=PLANNED_START,
            status_date=PLANNED_START + timedelta(days=20),
            phase_confidence=0.0,
        )
        assert result.status == "on_track"
        assert result.effective_spi_time == pytest.approx(1.0)
        assert result.forecast_delay_days == pytest.approx(0.0)


class TestCompute:
    def _command(self, **overrides) -> DelayForecastCommand:
        defaults = dict(
            plan_stages=[
                _stage("foundation", 0, 10),
                _stage("framing", 1, 7),
            ],
            planned_start=PLANNED_START,
            project_duration_days=17.0,
            current_phase="framing",
            as_of_date=PLANNED_START + timedelta(days=12),
            phase_confidence=1.0,
        )
        defaults.update(overrides)
        return DelayForecastCommand(**defaults)

    def test_no_matching_phase_returns_no_forecast(self):
        result = compute(self._command(current_phase="roofing"))
        assert result.status == "done"
        assert result.delay_days is None
        assert result.expected_completion is None

    def test_no_plan_stages_returns_no_forecast(self):
        result = compute(self._command(plan_stages=[], current_phase="framing"))
        assert result.status == "done"
        assert result.delay_days is None

    def test_phase_match_is_case_and_whitespace_insensitive(self):
        result = compute(self._command(current_phase="  FRAMING  "))
        assert result.status == "done"
        assert result.delay_days is not None

    def test_zero_project_duration_days_is_not_treated_as_missing(self):
        # Regression: `command.project_duration_days or activity_duration_sum`
        # used to conflate a legitimate 0.0 with "missing", silently falling
        # back to the (nonzero) activity-duration sum instead of using 0.
        result = compute(self._command(project_duration_days=0.0))
        assert result.status == "done"
        assert result.delay_days is None
        assert result.expected_completion is None

    def test_missing_project_duration_days_falls_back_to_activity_sum(self):
        # project_duration_days=None (genuinely missing) should still fall
        # back to the summed activity durations.
        result = compute(self._command(project_duration_days=None))
        assert result.status == "done"
        assert result.delay_days is not None

    def test_fractional_phase_offset_is_rounded_not_truncated(self, monkeypatch):
        # Regression: `planned_start + timedelta(days=phase_start_offset_days)`
        # only honors timedelta.days, silently flooring a fractional offset
        # instead of rounding it.
        captured = {}

        def fake_forecast_delay(**kwargs):
            captured.update(kwargs)
            return worker.DelayForecast(
                status="on_track",
                estimated_delay_to_date_days=0.0,
                forecast_delay_days=0.0,
                forecast_finish_date=PLANNED_START,
                effective_spi_time=1.0,
                confidence=1.0,
                reason="stub",
            )

        monkeypatch.setattr(worker, "forecast_delay", fake_forecast_delay)

        # project_duration_days=15 over stages summing to 17 -> scale
        # 15/17, offset for "framing" (after a 10-day foundation) is
        # 10 * 15/17 = 8.8235... days, which must round to 9, not floor to 8.
        compute(self._command(project_duration_days=15.0))

        assert captured["current_phase_started_at"] == PLANNED_START + timedelta(days=9)
