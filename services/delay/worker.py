"""Delay-forecast service — schedule lag (диаграмма: блок 4).

Consumes `delay.command` from the shared `csm.analysis` exchange, computes a
result, publishes it on `delay.result`. See backend/integrations/README.md
for the pipeline topology this is one leg of.

REAL forecast: `forecast_delay()` below is the Earned-Schedule-style model
from latency_prediction/delay_forecasting_baseline.ipynb, copied verbatim
(pure math, no heavy deps needed). `compute()` is the adapter from this
service's message shape to that function's arguments:

- `command.plan_stages` is phase-level durations only (see
  `PlanPhaseIn`/`schemas.py`) — the canonical plan format has no calendar
  dates (`planned_duration_days` per activity, not `planned_start`/
  `planned_end`; see
  phase_determination/data/Требования_к_каноническому_формату_плана_v2.docx).
  Summing `planned_duration_days` across a phase's activities overcounts
  whenever they run in parallel, so that sum isn't used as a day count
  directly — instead `phase_start_offset_days`/`phase_planned_duration_days`
  are computed as *fractions* of the total activity-duration sum, then
  rescaled onto `command.project_duration_days` (the canonical plan's
  `project_network_duration` — the real critical-path project timeline, see
  `plan_parser.py`). `planned_duration_days` passed to `forecast_delay()` is
  `project_duration_days` itself, not the inflated sum.
- `command.planned_start` (the one calendar-date anchor this needs) isn't
  part of the canonical plan either — it's the project's earliest journal
  entry date, resolved by the backend (see analysis.py's
  `_project_planned_start`) since the plan format intentionally carries no
  dates.
- `current_phase_started_at` comes from `command.current_phase_started_at`:
  the backend estimates it from the project's own phase history (see
  backend/progress.py's `estimate_phase_start`), as the notebook requires
  ("должен приходить из истории фаз, а не выдумываться модулем 4"). Only
  when that history has no earlier phase to measure from (None) does this
  fall back to `planned_start + phase_start_offset_days`. That fallback
  used to be the only behaviour, and it zeroed the forecast every time a
  new phase was detected: with the phase assumed to start on plan, earned
  schedule equals the calendar until the phase overruns its planned end.

No matching stage (empty plan, or `current_phase` not found in it) means
there's nothing to forecast from — returns a `done` result with
`delay_days=None`, which `analysis.py` already renders as "forecast
unavailable" rather than treating it as a service failure.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import date as date_type
from datetime import datetime, timedelta, timezone
from typing import Optional

import aio_pika

from schemas import DelayForecastCommand, DelayForecastResult, Envelope

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("csm.delay")

RABBITMQ_URL = os.environ.get("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")
EXCHANGE_NAME = "csm.analysis"  # must match backend/integrations/broker.py
COMMAND_ROUTING_KEY = "delay.command"
RESULT_ROUTING_KEY = "delay.result"
COMMAND_QUEUE = "delay.command.q"


@dataclass
class DelayForecast:
    status: str
    estimated_delay_to_date_days: Optional[float]
    forecast_delay_days: Optional[float]
    forecast_finish_date: Optional[date_type]
    effective_spi_time: Optional[float]
    confidence: float
    reason: str


def forecast_delay(
    *,
    planned_start: date_type,
    planned_duration_days: float,
    phase_start_offset_days: float,
    phase_planned_duration_days: float,
    current_phase_started_at: date_type,
    status_date: date_type,
    phase_confidence: float = 1.0,
    warning_threshold_days: float = 7.0,
    critical_threshold_days: float = 30.0,
) -> DelayForecast:
    if status_date < planned_start:
        return DelayForecast(
            status="not_started",
            estimated_delay_to_date_days=None,
            forecast_delay_days=None,
            forecast_finish_date=None,
            effective_spi_time=None,
            confidence=phase_confidence,
            reason="Status date is before planned project start.",
        )

    actual_time = (status_date - planned_start).days
    days_in_phase = max(0, (status_date - current_phase_started_at).days)

    if actual_time <= 0 or phase_planned_duration_days <= 0:
        return DelayForecast(
            status="insufficient_data",
            estimated_delay_to_date_days=None,
            forecast_delay_days=None,
            forecast_finish_date=None,
            effective_spi_time=None,
            confidence=phase_confidence,
            reason="Insufficient elapsed time or invalid phase duration.",
        )

    phase_progress = min(1.0, max(0.0, days_in_phase / phase_planned_duration_days))
    earned_schedule = phase_start_offset_days + phase_progress * phase_planned_duration_days
    spi_t = earned_schedule / actual_time

    # confidence=1 -> trust the phase detector fully; confidence=0 -> don't
    # deviate from a neutral SPI=1 (module 3's own doubt caps module 4's).
    effective_spi = 1.0 + phase_confidence * (spi_t - 1.0)

    if effective_spi <= 0:
        return DelayForecast(
            status="insufficient_data",
            estimated_delay_to_date_days=actual_time - earned_schedule,
            forecast_delay_days=None,
            forecast_finish_date=None,
            effective_spi_time=effective_spi,
            confidence=phase_confidence,
            reason="Unstable forecast; effective schedule index <= 0.",
        )

    estimated_delay_to_date = actual_time - earned_schedule
    forecast_total_duration = planned_duration_days / effective_spi
    forecast_delay_days = forecast_total_duration - planned_duration_days
    forecast_finish = planned_start.fromordinal(
        planned_start.toordinal() + round(forecast_total_duration)
    )

    if forecast_delay_days >= critical_threshold_days:
        status = "critical_delay"
    elif forecast_delay_days >= warning_threshold_days:
        status = "warning_delay"
    elif forecast_delay_days <= -warning_threshold_days:
        status = "ahead"
    else:
        status = "on_track"

    return DelayForecast(
        status=status,
        estimated_delay_to_date_days=estimated_delay_to_date,
        forecast_delay_days=forecast_delay_days,
        forecast_finish_date=forecast_finish,
        effective_spi_time=effective_spi,
        confidence=phase_confidence,
        reason="Earned-Schedule-style mathematical forecast.",
    )


def compute(command: DelayForecastCommand) -> DelayForecastResult:  # EXTENSION POINT
    stages = sorted(command.plan_stages, key=lambda s: s.phase_order)
    matched = next(
        (s for s in stages if s.phase.strip().lower() == command.current_phase.strip().lower()),
        None,
    )
    if not stages or matched is None:
        log.info(
            "no plan stage matches current_phase=%r (%d stages) — no forecast",
            command.current_phase,
            len(stages),
        )
        return DelayForecastResult(status="done", confidence=command.phase_confidence)

    # The canonical plan only has durations, not calendar dates (see
    # plan_parser.py). Summed activity durations overcount whenever a
    # phase's activities run in parallel, so offsets are computed as
    # *fractions* of that sum and rescaled onto the real project timeline
    # (project_duration_days, i.e. project_network_duration) rather than
    # used as day counts directly.
    activity_duration_sum = sum(s.planned_duration_days for s in stages)
    planned_duration_days = (
        command.project_duration_days
        if command.project_duration_days is not None
        else activity_duration_sum
    )
    scale = planned_duration_days / activity_duration_sum if activity_duration_sum else 1.0

    phase_start_offset_days = (
        sum(s.planned_duration_days for s in stages if s.phase_order < matched.phase_order)
        * scale
    )
    phase_planned_duration_days = matched.planned_duration_days * scale
    # date + timedelta only honors timedelta.days, so a fractional offset
    # must be rounded first or it's silently floored (dropping up to
    # ~1 day) rather than rounded.
    # Planned start only as a fallback — see the module docstring.
    current_phase_started_at = command.current_phase_started_at or (
        command.planned_start + timedelta(days=round(phase_start_offset_days))
    )
    log.info(
        "found phase %r at offset %.1f/%.0f days (scale %.2fx from activity-duration sum), "
        "started %s (%s), status date %s",
        matched.phase,
        phase_start_offset_days,
        planned_duration_days,
        scale,
        current_phase_started_at.isoformat(),
        "observed" if command.current_phase_started_at else "assumed on plan",
        command.as_of_date.isoformat(),
    )

    forecast = forecast_delay(
        planned_start=command.planned_start,
        planned_duration_days=planned_duration_days,
        phase_start_offset_days=phase_start_offset_days,
        phase_planned_duration_days=phase_planned_duration_days,
        current_phase_started_at=current_phase_started_at,
        status_date=command.as_of_date,
        phase_confidence=command.phase_confidence,
    )
    delay_days = (
        round(forecast.forecast_delay_days) if forecast.forecast_delay_days is not None else None
    )
    log.info(
        "found forecast %s: %s days delay, expected completion %s, effective SPI %.2f (%s)",
        forecast.status,
        delay_days,
        forecast.forecast_finish_date,
        forecast.effective_spi_time if forecast.effective_spi_time is not None else float("nan"),
        forecast.reason,
    )
    return DelayForecastResult(
        status="done",
        delay_days=delay_days,
        expected_completion=forecast.forecast_finish_date,
        confidence=forecast.confidence,
        spi_time=forecast.effective_spi_time,
    )


async def main() -> None:
    connection = await aio_pika.connect_robust(RABBITMQ_URL)
    async with connection:
        channel = await connection.channel()
        await channel.set_qos(prefetch_count=10)
        exchange = await channel.declare_exchange(
            EXCHANGE_NAME, aio_pika.ExchangeType.TOPIC, durable=True
        )
        queue = await channel.declare_queue(COMMAND_QUEUE, durable=True)
        await queue.bind(exchange, COMMAND_ROUTING_KEY)

        async def on_message(message: aio_pika.abc.AbstractIncomingMessage) -> None:
            async with message.process():
                envelope = Envelope[DelayForecastCommand].model_validate_json(message.body)
                try:
                    result = compute(envelope.payload)
                except Exception as exc:  # noqa: BLE001 - reported back as a failed result
                    log.exception("compute failed for asset %s", envelope.correlation_id)
                    result = DelayForecastResult(status="failed", error=str(exc))

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
        log.info("delay worker consuming %s from %s", COMMAND_ROUTING_KEY, COMMAND_QUEUE)
        await asyncio.Future()  # run forever


if __name__ == "__main__":
    asyncio.run(main())
