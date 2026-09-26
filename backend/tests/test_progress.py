import io
import json
import uuid
from datetime import date, datetime, timedelta, timezone

import openpyxl

import analysis
from integrations import broker
from integrations.schemas import Envelope, PhaseResult, VisualPhaseResult
from models import JournalEntry
from progress import PhaseObservation, estimate_phase_start
from tests.conftest import _fake_publish
from tests.test_media import VIDEO, _project

CSV_PLAN = ("plan.csv", b"phase,duration_days\nEarthwork,30\n", "text/csv")


async def _plan_and_entry(client, headers, username) -> str:
    pid = await _project(client, headers)
    await client.post(f"/projects/{pid}/plan", headers=headers, files=[("file", CSV_PLAN)])
    await client.post(
        f"/projects/{pid}/entries",
        headers=headers,
        data={"author": username, "date": "2026-09-01"},
        files=[("files", VIDEO)],
    )
    return pid


def _sheet_rows(content: bytes, name: str) -> list[tuple]:
    workbook = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    return list(workbook[name].iter_rows(values_only=True))


async def test_canonical_export_carries_actuals_and_history(client, auth):
    username, headers = await auth()
    pid = await _plan_and_entry(client, headers, username)

    content = (await client.get(f"/projects/{pid}/plan/canonical", headers=headers)).content

    # conftest's fake phase service always reports Earthwork — the plan's
    # only phase — so it's the one in progress.
    activities = _sheet_rows(content, "activities")
    status_col = activities[0].index("status")
    assert activities[1][status_col] == "In Progress"

    actuals = _sheet_rows(content, "actuals")
    row = dict(zip(actuals[0], actuals[1]))
    assert row["phase"] == "Earthwork"
    assert row["first_detected"].date().isoformat() == "2026-09-01"
    assert row["planned_start"].date().isoformat() == "2026-09-01"
    assert row["start_deviation_days"] == 0

    history = _sheet_rows(content, "history")
    assert len(history) == 2
    entry = dict(zip(history[0], history[1]))
    assert entry["phase"] == "Earthwork"
    assert entry["delay_days"] == 2


async def test_close_project_freezes_final_report_and_blocks_changes(client, auth):
    username, headers = await auth()
    pid = await _plan_and_entry(client, headers, username)

    closed = await client.post(f"/projects/{pid}/close", headers=headers)
    assert closed.status_code == 200
    body = closed.json()
    assert body["closedAt"]
    report = body["finalReport"]
    assert report["name"].endswith("_final_report.xlsx")

    served = await client.get(f"/files/{report['id']}")
    assert served.status_code == 200
    assert _sheet_rows(served.content, "history")[1][1] == "Earthwork"

    assert (await client.post(f"/projects/{pid}/close", headers=headers)).status_code == 409
    assert (
        await client.post(
            f"/projects/{pid}/entries",
            headers=headers,
            data={"author": username, "date": "2026-09-02"},
            files=[("files", VIDEO)],
        )
    ).status_code == 409
    assert (
        await client.post(f"/projects/{pid}/plan", headers=headers, files=[("file", CSV_PLAN)])
    ).status_code == 409
    assert (await client.delete(f"/projects/{pid}/plan", headers=headers)).status_code == 409


async def test_close_project_without_plan_freezes_empty_report(client, auth):
    _, headers = await auth()
    pid = await _project(client, headers)
    closed = await client.post(f"/projects/{pid}/close", headers=headers)
    assert closed.status_code == 200
    report = closed.json()["finalReport"]
    served = await client.get(f"/files/{report['id']}")
    for name in ("activities", "actuals", "history"):
        assert len(_sheet_rows(served.content, name)) == 1  # header only


def _obs(day: int, phase: str, confidence: float = 0.9) -> PhaseObservation:
    return PhaseObservation(date=date(2026, 3, 1) + timedelta(days=day), phase=phase, confidence=confidence)


def test_phase_start_is_midpoint_of_the_switch_gap():
    history = [_obs(0, "Earthwork"), _obs(7, "Earthwork"), _obs(14, "Foundation"), _obs(21, "Foundation")]
    assert estimate_phase_start(history, "Foundation") == date(2026, 3, 11)


def test_phase_start_ignores_low_confidence_flicker():
    history = [
        _obs(0, "Earthwork"),
        _obs(10, "Foundation"),
        _obs(20, "Earthwork", confidence=0.3),  # noise, not a real switch back
        _obs(30, "Foundation"),
    ]
    assert estimate_phase_start(history, "Foundation") == date(2026, 3, 6)


def test_phase_start_unknown_without_an_earlier_phase():
    assert estimate_phase_start([_obs(0, "Earthwork"), _obs(7, "Earthwork")], "Earthwork") is None


async def test_delay_command_carries_observed_phase_start(client, auth, monkeypatch):
    """Regression for the forecast resetting to 0 on every phase change: once
    the detector moves on to a new phase, delay.command must carry when that
    phase started on site, not leave the delay service to assume it started
    on plan."""
    username, headers = await auth()
    pid = await _project(client, headers)
    await client.post(f"/projects/{pid}/plan", headers=headers, files=[("file", CSV_PLAN)])

    phase_by_date = {"2026-09-01": "Earthwork", "2026-09-15": "Foundation"}
    delay_commands: list[dict] = []

    async def publish(routing_key: str, body: bytes) -> None:
        payload = json.loads(body)["payload"]
        if routing_key == broker.PHASE_COMMAND:
            await analysis.handle_phase_result(
                Envelope(
                    correlation_id=uuid.UUID(json.loads(body)["correlation_id"]),
                    published_at=datetime.now(timezone.utc),
                    payload=PhaseResult(
                        status="done", phase_name=phase_by_date[payload["as_of_date"]], confidence=0.9
                    ),
                )
            )
            return
        if routing_key == broker.VISUAL_PHASE_COMMAND:
            # визуальный сигнал согласован с техникой: тест про дату начала
            # фазы в delay.command, а не про их спор в ансамбле
            entry_id = uuid.UUID(json.loads(body)["correlation_id"])
            async with analysis.SessionLocal() as db:
                entry_date = (await db.get(JournalEntry, entry_id)).date.isoformat()
            await analysis.handle_visual_phase_result(
                Envelope(
                    correlation_id=entry_id,
                    published_at=datetime.now(timezone.utc),
                    payload=VisualPhaseResult(status="done", phase_name=phase_by_date[entry_date], confidence=0.7),
                )
            )
            return
        if routing_key == broker.DELAY_COMMAND:
            delay_commands.append(payload)
        await _fake_publish(routing_key, body)

    monkeypatch.setattr(broker, "publish", publish)
    for day in phase_by_date:
        await client.post(
            f"/projects/{pid}/entries",
            headers=headers,
            data={"author": username, "date": day},
            files=[("files", VIDEO)],
        )

    assert [c["current_phase"] for c in delay_commands] == ["Earthwork", "Foundation"]
    assert delay_commands[0]["current_phase_started_at"] is None
    assert delay_commands[1]["current_phase_started_at"] == "2026-09-08"


async def test_timeline_serves_chart_series(client, auth):
    username, headers = await auth()
    pid = await _plan_and_entry(client, headers, username)

    timeline = (await client.get(f"/projects/{pid}/timeline", headers=headers)).json()

    # conftest's canned plan is one 30-day Earthwork phase inside a 120-day
    # network; anchored on the only entry's date.
    assert timeline["plannedStart"] == "2026-09-01"
    assert timeline["plannedFinish"] == "2026-12-30"
    [phase] = timeline["phases"]
    assert phase["phase"] == "Earthwork"
    assert phase["status"] == "In Progress"
    assert phase["plannedStart"] == "2026-09-01"
    assert phase["firstDetected"] == "2026-09-01"
    [point] = timeline["points"]
    assert point["phase"] == "Earthwork"
    assert point["delayDays"] == 2
    assert point["spiTime"] == 0.95


async def test_timeline_without_plan_still_returns_points(client, auth):
    username, headers = await auth()
    pid = await _project(client, headers)
    await client.post(
        f"/projects/{pid}/entries",
        headers=headers,
        data={"author": username, "date": "2026-09-01"},
        files=[("files", VIDEO)],
    )
    timeline = (await client.get(f"/projects/{pid}/timeline", headers=headers)).json()
    assert timeline["plannedStart"] is None
    assert timeline["phases"] == []
    assert len(timeline["points"]) == 1
