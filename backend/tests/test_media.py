import io
import json
import uuid
from datetime import datetime, timezone

import openpyxl
from sqlalchemy import select

import analysis
from integrations.schemas import Envelope, PlanNormalizeResult
from models import EquipmentObservation, PlanStage
from plan_parser import CANONICAL_COLUMNS

VIDEO = ("clip.mp4", b"\x00\x00\x00 ftypisom" + b"\x00" * 64, "video/mp4")


async def _project(client, headers) -> str:
    return (
        await client.post("/projects", headers=headers, json={"name": "Объект"})
    ).json()["id"]


def _canonical_plan_xlsx(rows: list[dict]) -> bytes:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "activities"
    sheet.append(CANONICAL_COLUMNS)
    for row in rows:
        sheet.append([row[c] for c in CANONICAL_COLUMNS])
    buf = io.BytesIO()
    workbook.save(buf)
    return buf.getvalue()


async def _plan_stages(pid: str) -> list[PlanStage]:
    async with analysis.SessionLocal() as db:
        return (
            (await db.execute(select(PlanStage).where(PlanStage.project_id == uuid.UUID(pid))))
            .scalars()
            .all()
        )


async def _equipment_observations(pid: str) -> list[tuple]:
    async with analysis.SessionLocal() as db:
        rows = (
            await db.execute(
                select(EquipmentObservation)
                .where(EquipmentObservation.project_id == uuid.UUID(pid))
                .order_by(EquipmentObservation.date)
            )
        ).scalars().all()
        # Detach the values we need before the session closes.
        return [(r.date, json.loads(r.counts)) for r in rows]


async def test_plan_upload_download_delete(client, auth):
    _, headers = await auth()
    pid = await _project(client, headers)

    content = b"phase,duration_days\nEarthwork,30\n"
    up = await client.post(
        f"/projects/{pid}/plan",
        headers=headers,
        files=[("file", ("plan.csv", content, "text/csv"))],
    )
    assert up.status_code == 200
    plan = up.json()
    assert plan["kind"] == "table"
    # This .csv isn't the canonical .xlsx shape, so upload_plan falls back to
    # the planner's LLM normalization — conftest's fake broker runs that
    # round trip in-process, synchronously, so it's already done here.
    assert plan["insight"]["status"] == "ready"
    assert len(await _plan_stages(pid)) == 1

    project = (await client.get(f"/projects/{pid}", headers=headers)).json()
    assert project["plan"]["id"] == plan["id"]
    assert project["plan"]["insight"]["status"] == "ready"

    served = await client.get(plan["url"].replace("http://localhost:8000", ""))
    assert served.status_code == 200
    assert served.content == content

    assert (await client.delete(f"/projects/{pid}/plan", headers=headers)).status_code == 204
    assert (await client.get(f"/projects/{pid}", headers=headers)).json()["plan"] is None
    assert await _plan_stages(pid) == []


async def test_canonical_plan_xlsx_skips_llm_fallback(client, auth):
    _, headers = await auth()
    pid = await _project(client, headers)

    base = {
        "project_id": pid,
        "resource_type": "equipment",
        "quantity": 1,
        "unit_cost": 1,
        "planned_cost": 1,
        "criticality": 0.5,
        "status": "Planned",
        "critical_path": 1,
        "critical_path_position": 1,
        "project_network_duration": 100,
        "predecessor_count": 0,
        "successor_count": 0,
        "split": "validation",
        "expected_equipment": "['worker']",
        "expected_equipment_descriptions": "{}",
    }
    content = _canonical_plan_xlsx(
        [
            {**base, "activity_id": "A1", "phase": "Preconstruction", "phase_order": 1,
             "activity_sequence": 1, "activity_name": "Design", "planned_duration_days": 10},
            {**base, "activity_id": "A2", "phase": "Earthwork", "phase_order": 3,
             "activity_sequence": 1, "activity_name": "Excavation", "planned_duration_days": 20},
        ]
    )

    up = await client.post(
        f"/projects/{pid}/plan",
        headers=headers,
        files=[(
            "file",
            (
                "plan.xlsx",
                content,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ),
        )],
    )
    assert up.status_code == 200
    plan = up.json()
    # Already canonical — parsed synchronously, no planner/LLM round trip.
    assert plan["insight"]["status"] == "ready"

    stages = {s.phase: s.planned_duration_days for s in await _plan_stages(pid)}
    assert stages == {"Preconstruction": 10, "Earthwork": 20}


async def test_canonical_plan_download_round_trips(client, auth):
    _, headers = await auth()
    pid = await _project(client, headers)

    up = await client.post(
        f"/projects/{pid}/plan",
        headers=headers,
        files=[("file", ("plan.csv", b"phase,duration_days\nEarthwork,30\n", "text/csv"))],
    )
    assert up.json()["insight"]["status"] == "ready"

    download = await client.get(f"/projects/{pid}/plan/canonical", headers=headers)
    assert download.status_code == 200
    assert download.headers["content-type"] == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

    # Re-uploading the exported file should parse as canonical directly —
    # no second LLM round trip, same phases/duration as before.
    other_pid = await _project(client, headers)
    reupload = await client.post(
        f"/projects/{other_pid}/plan",
        headers=headers,
        files=[(
            "file",
            (
                "plan_canonical.xlsx",
                download.content,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ),
        )],
    )
    assert reupload.status_code == 200
    assert reupload.json()["insight"]["status"] == "ready"
    stages = {s.phase: s.planned_duration_days for s in await _plan_stages(other_pid)}
    assert stages == {"Earthwork": 30}


async def test_canonical_plan_download_404s_before_plan_ready(client, auth):
    _, headers = await auth()
    pid = await _project(client, headers)
    assert (
        await client.get(f"/projects/{pid}/plan/canonical", headers=headers)
    ).status_code == 404


async def test_plan_normalization_failure_marks_plan_failed(client, auth):
    _, headers = await auth()
    pid = await _project(client, headers)

    up = await client.post(
        f"/projects/{pid}/plan",
        headers=headers,
        files=[("file", ("plan.csv", b"phase,duration_days\nEarthwork,30\n", "text/csv"))],
    )
    plan_id = up.json()["id"]

    # Simulate the planner service reporting a bad/unparseable model
    # response instead of the canned success conftest's fake broker gave it.
    await analysis.handle_plan_result(
        Envelope(
            correlation_id=uuid.UUID(plan_id),
            published_at=datetime.now(timezone.utc),
            payload=PlanNormalizeResult(status="failed", error="model returned invalid JSON"),
        )
    )

    project = (await client.get(f"/projects/{pid}", headers=headers)).json()
    assert project["plan"]["insight"]["status"] == "failed"


async def test_plan_rejects_non_document(client, auth):
    _, headers = await auth()
    pid = await _project(client, headers)
    bad = await client.post(
        f"/projects/{pid}/plan",
        headers=headers,
        files=[("file", ("notes.txt", b"hello", "text/plain"))],
    )
    assert bad.status_code == 422


async def test_video_upload_runs_analysis_and_fills_plan_status(client, auth):
    username, headers = await auth()
    pid = await _project(client, headers)

    entry = await client.post(
        f"/projects/{pid}/entries",
        headers=headers,
        data={"author": username, "date": "2026-09-01"},
        files=[("files", VIDEO)],
    )
    assert entry.status_code == 201
    media = entry.json()["media"]
    assert len(media) == 1
    video_id = media[0]["id"]

    # conftest fakes the broker so the whole vision→phase→delay chain runs
    # in-process, synchronously — the background task has completed by now.
    got = (await client.get(f"/projects/{pid}/videos/{video_id}", headers=headers)).json()
    assert got["insight"]["status"] == "ready"
    assert got["insight"]["stageSummary"]
    assert got["insight"]["equipmentSummary"]
    assert got["insight"]["photos"] == []

    project = (await client.get(f"/projects/{pid}", headers=headers)).json()
    assert project["planStatus"] == got["insight"]["stageSummary"]

    served = await client.get(f"/files/{video_id}")
    assert served.status_code == 200


async def test_video_analysis_accumulates_daily_equipment_history(client, auth):
    """Each analysed video/photo should feed the phase model a real day-by-
    day history (see analysis.py's _upsert_equipment_observation /
    services/phase/worker.py) instead of only its own single-timestep
    counts — two videos on different days become two daily rows; two on the
    same day merge (max per class, not sum) into one."""
    username, headers = await auth()
    pid = await _project(client, headers)

    async def _upload(date: str) -> None:
        resp = await client.post(
            f"/projects/{pid}/entries",
            headers=headers,
            data={"author": username, "date": date},
            files=[("files", VIDEO)],
        )
        assert resp.status_code == 201

    await _upload("2026-09-01")
    await _upload("2026-09-01")  # same day — should merge, not add a row
    await _upload("2026-09-03")

    observations = await _equipment_observations(pid)
    dates = [d.isoformat() for d, _ in observations]
    assert dates == ["2026-09-01", "2026-09-03"]
    # conftest's fake vision result is always excavator:1 — merging two same-
    # day videos of it via max should still read 1, not 2.
    assert observations[0][1] == {"excavator": 1}
    assert observations[1][1] == {"excavator": 1}


async def test_photo_upload_runs_analysis_and_fills_plan_status(client, auth):
    username, headers = await auth()
    pid = await _project(client, headers)

    entry = await client.post(
        f"/projects/{pid}/entries",
        headers=headers,
        data={"author": username, "date": "2026-09-01"},
        files=[("files", ("photo.jpg", b"\xff\xd8\xff", "image/jpeg"))],
    )
    assert entry.status_code == 201
    media = entry.json()["media"]
    assert len(media) == 1
    assert media[0]["kind"] == "image"
    photo_id = media[0]["id"]

    # conftest fakes the broker so the whole vision→phase→delay chain runs
    # in-process, synchronously — the background task has completed by now.
    got = (await client.get(f"/projects/{pid}/videos/{photo_id}", headers=headers)).json()
    assert got["insight"]["status"] == "ready"
    assert got["insight"]["stageSummary"]
    assert got["insight"]["equipmentSummary"]

    project = (await client.get(f"/projects/{pid}", headers=headers)).json()
    assert project["planStatus"] == got["insight"]["stageSummary"]


async def test_entry_rejects_non_media(client, auth):
    username, headers = await auth()
    pid = await _project(client, headers)
    bad = await client.post(
        f"/projects/{pid}/entries",
        headers=headers,
        data={"author": username, "date": "2026-09-01"},
        files=[("files", ("notes.txt", b"hello", "text/plain"))],
    )
    assert bad.status_code == 422


async def test_video_and_plan_scoped_to_owner(client, auth):
    alice_name, alice = await auth("alice")
    _, bob = await auth("bob")
    pid = await _project(client, alice)

    entry = await client.post(
        f"/projects/{pid}/entries",
        headers=alice,
        data={"author": alice_name, "date": "2026-09-01"},
        files=[("files", VIDEO)],
    )
    video_id = entry.json()["media"][0]["id"]

    assert (
        await client.get(f"/projects/{pid}/videos/{video_id}", headers=bob)
    ).status_code == 404
    assert (
        await client.post(
            f"/projects/{pid}/plan",
            headers=bob,
            files=[("file", ("p.pdf", b"%PDF", "application/pdf"))],
        )
    ).status_code == 404


async def test_delete_entry_removes_file_from_disk(client, auth):
    from config import settings

    username, headers = await auth()
    pid = await _project(client, headers)
    entry = await client.post(
        f"/projects/{pid}/entries",
        headers=headers,
        data={"author": username, "date": "2026-09-01"},
        files=[("files", VIDEO)],
    )
    entry_id = entry.json()["id"]
    video_id = entry.json()["media"][0]["id"]

    files = list((settings.media_root / pid).glob(f"{video_id}*"))
    assert files and files[0].is_file()

    assert (
        await client.delete(f"/projects/{pid}/entries/{entry_id}", headers=headers)
    ).status_code == 204
    assert not files[0].is_file()
    assert (await client.get(f"/files/{video_id}")).status_code == 404
