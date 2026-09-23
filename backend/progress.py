"""Plan-vs-actual "fact" export — the canonical plan plus what the journal
has observed so far.

The uploaded plan (`PlanStage`) is the baseline and is never rewritten by
analysis: delay_days is measured against it, so mutating it mid-project
would leave nothing to measure against. Instead this module *derives* the
current fact from the baseline plus the journal every time it's asked,
and `build_progress_workbook` renders it as one `.xlsx`:

- `activities` — the canonical sheet (plan_parser.build_canonical_workbook)
  with its `status` column filled from the journal instead of always
  "Planned". Re-uploading the file still round-trips through
  `parse_plan_workbook`, which only reads this sheet.
- `actuals` — one row per phase: planned window on the calendar vs when
  the phase detector first/last reported it, and when it most likely
  started (`estimate_phase_start`, the same estimate the delay forecast
  now uses).
- `history` — one row per analysed journal entry: phase, confidence,
  delay, expected completion — the raw series behind the delay charts.

Served live by `GET /projects/{id}/plan/canonical`, and frozen once as the
project's `final_report` asset by `POST /projects/{id}/close` (see
routers/projects.py).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta

from models import JournalEntry, PlanStage, Project
from plan_parser import CanonicalExportPhase, build_canonical_workbook, workbook_bytes

# A different-phase reading below this confidence is treated as detector
# noise when looking for where the current phase began — see
# estimate_phase_start. Same 0..1 scale as PhaseResult.confidence.
PHASE_SWITCH_MIN_CONFIDENCE = 0.5

STATUS_COMPLETED = "Completed"
STATUS_IN_PROGRESS = "In Progress"
STATUS_PLANNED = "Planned"


@dataclass
class PhaseWindow:
    phase: str
    phase_order: int
    start_offset_days: float
    duration_days: float
    planned_start: date | None
    planned_end: date | None


def phase_windows(
    stages: list[PlanStage], project_duration_days: float, planned_start: date | None
) -> list[PhaseWindow]:
    """Each phase's planned window on the real project timeline. Same
    rescaling as services/delay/worker.py's `compute()` (phase durations as
    fractions of the activity-duration sum, stretched onto
    `project_network_duration`) — keep the two in sync, otherwise the
    export's planned dates disagree with what delay_days was computed from.
    `planned_start` is the same anchor too (the project's earliest journal
    entry, see analysis.py's `_project_planned_start`); None means no
    calendar dates, only offsets."""
    ordered = sorted(stages, key=lambda s: s.phase_order)
    total = sum(s.planned_duration_days for s in ordered)
    scale = project_duration_days / total if total else 1.0
    windows: list[PhaseWindow] = []
    offset = 0.0
    for s in ordered:
        duration = s.planned_duration_days * scale
        windows.append(
            PhaseWindow(
                phase=s.phase,
                phase_order=s.phase_order,
                start_offset_days=offset,
                duration_days=duration,
                planned_start=planned_start + timedelta(days=round(offset)) if planned_start else None,
                planned_end=(
                    planned_start + timedelta(days=round(offset + duration)) if planned_start else None
                ),
            )
        )
        offset += duration
    return windows


@dataclass
class PhaseObservation:
    date: date
    phase: str
    confidence: float | None


def estimate_phase_start(
    observations: list[PhaseObservation], current_phase: str
) -> date | None:
    """When `current_phase` actually started on site, from the project's
    phase history up to and including the observation just made — what
    the delay model's `current_phase_started_at` needs (see
    services/delay/worker.py).

    Walks back through the uninterrupted run of `current_phase` readings to
    the most recent confident reading of some *other* phase, and returns the
    midpoint between that reading and the run's first day: the switch
    happened somewhere in that gap, so the midpoint's error is at most half
    the interval between site visits. The first detection alone would
    always land late by up to a whole interval.

    A different-phase reading under PHASE_SWITCH_MIN_CONFIDENCE doesn't end
    the run — one noisy visit (Frame → Masonry → Frame) mustn't reset the
    phase's start. None if no earlier phase was ever confidently observed
    (the project has been in this phase since its first entry): there's
    nothing to measure from, and the caller falls back to the plan."""
    current = current_phase.strip().lower()
    run_start: date | None = None
    for obs in sorted(observations, key=lambda o: o.date, reverse=True):
        if obs.phase.strip().lower() == current:
            run_start = obs.date
            continue
        if (obs.confidence or 0.0) < PHASE_SWITCH_MIN_CONFIDENCE:
            continue
        if run_start is None:
            return None
        return obs.date + (run_start - obs.date) // 2
    return None


def analysed_entries(entries: list[JournalEntry]) -> list[JournalEntry]:
    """Entries with a finished phase/delay analysis, in site-date order —
    one point per entry on the delay charts, one row in `history`."""
    return sorted(
        (e for e in entries if e.analysis_status == "ready" and e.phase_name),
        key=lambda e: (e.date, e.analyzed_at or e.created_at),
    )


def phase_statuses(stages: list[PlanStage], entries: list[JournalEntry]) -> dict[str, str]:
    """Phases before the most recently observed one are Completed, it is In
    Progress, the rest Planned. Keyed on the latest analysed entry (by site
    date), not on the furthest phase ever seen — a later visit that reads
    as an earlier phase is taken at face value rather than second-guessed
    here."""
    analysed = analysed_entries(entries)
    if not analysed:
        return {}
    current = analysed[-1].phase_name.strip().lower()
    current_order = next(
        (s.phase_order for s in stages if s.phase.strip().lower() == current), None
    )
    if current_order is None:
        return {}
    return {
        s.phase: (
            STATUS_COMPLETED
            if s.phase_order < current_order
            else STATUS_IN_PROGRESS if s.phase_order == current_order else STATUS_PLANNED
        )
        for s in stages
    }


@dataclass
class PhaseActual:
    """One phase's plan vs what the journal has seen — shared by the
    `actuals` sheet and the project timeline (graph C), so both show the
    same numbers."""

    phase: str
    phase_order: int
    status: str
    window: PhaseWindow
    first_detected: date | None
    last_detected: date | None
    # Same estimate the delay forecast uses (see estimate_phase_start),
    # taken as of the phase's first detection.
    estimated_start: date | None

    @property
    def start_deviation_days(self) -> int | None:
        """Falls back to the first detection when there's no earlier phase
        to measure an estimate from."""
        started = self.estimated_start or self.first_detected
        if started is None or self.window.planned_start is None:
            return None
        return (started - self.window.planned_start).days


def planned_start_of(entries: list[JournalEntry]) -> date | None:
    """The calendar anchor for the plan — the project's earliest journal
    entry, same as analysis.py's `_project_planned_start`."""
    return min((e.date for e in entries), default=None)


def phase_actuals(
    stages: list[PlanStage], entries: list[JournalEntry], project_duration_days: float
) -> list[PhaseActual]:
    stages = sorted(stages, key=lambda s: s.phase_order)
    statuses = phase_statuses(stages, entries)
    analysed = analysed_entries(entries)
    detected: dict[str, list[date]] = {}
    for e in analysed:
        detected.setdefault(e.phase_name.strip().lower(), []).append(e.date)
    observations = [
        PhaseObservation(date=e.date, phase=e.phase_name, confidence=e.phase_confidence)
        for e in analysed
    ]
    out: list[PhaseActual] = []
    for w in phase_windows(stages, project_duration_days, planned_start_of(entries)):
        seen = detected.get(w.phase.strip().lower(), [])
        first = min(seen) if seen else None
        out.append(
            PhaseActual(
                phase=w.phase,
                phase_order=w.phase_order,
                status=statuses.get(w.phase, STATUS_PLANNED),
                window=w,
                first_detected=first,
                last_detected=max(seen) if seen else None,
                estimated_start=(
                    estimate_phase_start([o for o in observations if o.date <= first], w.phase)
                    if first
                    else None
                ),
            )
        )
    return out


def build_progress_workbook(
    project: Project, stages: list[PlanStage], entries: list[JournalEntry]
) -> bytes:
    """Always renders — no plan yet (none uploaded, or the LLM fallback still
    running) just leaves `activities`/`actuals` as bare headers, and no
    analysed entries leaves `history` empty."""
    if project.plan_duration_days is None:
        stages = []
    stages = sorted(stages, key=lambda s: s.phase_order)
    actuals_rows = phase_actuals(stages, entries, project.plan_duration_days) if stages else []
    workbook = build_canonical_workbook(
        str(project.id),
        [
            CanonicalExportPhase(
                phase=s.phase,
                phase_order=s.phase_order,
                planned_duration_days=s.planned_duration_days,
                expected_equipment=json.loads(s.expected_equipment or "[]"),
            )
            for s in stages
        ],
        project.plan_duration_days or 0.0,
        statuses={a.phase: a.status for a in actuals_rows},
    )

    actuals = workbook.create_sheet("actuals")
    actuals.append(
        [
            "phase_order",
            "phase",
            "status",
            "planned_start",
            "planned_end",
            "planned_duration_days",
            "first_detected",
            "last_detected",
            "estimated_start",
            "start_deviation_days",
        ]
    )
    for a in actuals_rows:
        actuals.append(
            [
                a.phase_order,
                a.phase,
                a.status,
                a.window.planned_start,
                a.window.planned_end,
                round(a.window.duration_days, 1),
                a.first_detected,
                a.last_detected,
                a.estimated_start,
                a.start_deviation_days,
            ]
        )

    history = workbook.create_sheet("history")
    history.append(
        [
            "date",
            "phase",
            "phase_confidence",
            "visual_phase",
            "delay_days",
            "expected_completion",
            "spi_time",
            "author",
        ]
    )
    for e in analysed_entries(entries):
        history.append(
            [
                e.date,
                e.phase_name,
                round(e.phase_confidence, 3) if e.phase_confidence is not None else None,
                e.visual_phase_name,
                e.delay_days,
                e.expected_completion,
                round(e.spi_time, 3) if e.spi_time is not None else None,
                e.author,
            ]
        )

    return workbook_bytes(workbook)
