"""Оркестрация объединения техники и изображения (analysis.py): phase.command
уходит только когда есть и детектор по всем файлам, и визуальный результат
(или его тайм-аут), и несёт визуальную историю проекта. Само объединение —
в services/phase/fusion.py (тесты — services/phase/test_fusion.py)."""

import json
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import select

import analysis
from integrations import broker
from integrations.schemas import Envelope, PhaseCommand, PhaseResult, VisualPhaseResult
from models import JournalEntry
from tests.conftest import _fake_publish
from tests.test_media import VIDEO, _project

FOUNDATION = {"Earthwork": 0.2, "Foundation": 0.7, "Structural Frame": 0.1}


def _publisher(visual: VisualPhaseResult | None, sent: list[PhaseCommand], phase: PhaseResult | None = None):
    """_fake_publish, но с заданным ответом visual_phase (None — не отвечает
    вовсе) и с записью каждой отправленной phase.command."""

    async def publish(routing_key: str, body: bytes) -> None:
        envelope = json.loads(body)
        correlation_id = uuid.UUID(envelope["correlation_id"])
        now = datetime.now(timezone.utc)
        if routing_key == broker.VISUAL_PHASE_COMMAND:
            if visual is not None:
                await analysis.handle_visual_phase_result(
                    Envelope(correlation_id=correlation_id, published_at=now, payload=visual)
                )
            return
        if routing_key == broker.PHASE_COMMAND:
            sent.append(PhaseCommand.model_validate(envelope["payload"]))
            if phase is not None:
                await analysis.handle_phase_result(Envelope(correlation_id=correlation_id, published_at=now, payload=phase))
                return
        await _fake_publish(routing_key, body)

    return publish


async def _post_entry(client, headers, username, pid, day="2026-09-01"):
    await client.post(
        f"/projects/{pid}/entries",
        headers=headers,
        data={"author": username, "date": day},
        files=[("files", VIDEO)],
    )


async def _entries(db) -> list[JournalEntry]:
    return list((await db.execute(select(JournalEntry).order_by(JournalEntry.date))).scalars().all())


async def test_phase_command_carries_the_visual_reading(client, auth, monkeypatch):
    username, headers = await auth()
    pid = await _project(client, headers)
    sent: list[PhaseCommand] = []
    visual = VisualPhaseResult(status="done", phase_name="Foundation", confidence=0.7, phase_probs=FOUNDATION)
    monkeypatch.setattr(broker, "publish", _publisher(visual, sent))

    await _post_entry(client, headers, username, pid)

    assert len(sent) == 1
    assert [(v.date, v.phase_probs) for v in sent[0].visual_history] == [(date(2026, 9, 1), FOUNDATION)]
    async with analysis.SessionLocal() as db:
        (entry,) = await _entries(db)
        assert json.loads(entry.visual_phase_details)["phase_probs"] == FOUNDATION
        assert (entry.visual_phase_name, entry.analysis_status) == ("Foundation", "ready")


async def test_visual_history_spans_the_project_and_skips_unusable_readings(client, auth, monkeypatch):
    username, headers = await auth()
    pid = await _project(client, headers)
    sent: list[PhaseCommand] = []
    earth = {"Earthwork": 0.8, "Foundation": 0.2}

    monkeypatch.setattr(broker, "publish", _publisher(
        VisualPhaseResult(status="done", phase_name="Earthwork", confidence=0.8, phase_probs=earth), sent
    ))
    await _post_entry(client, headers, username, pid, "2026-08-01")
    monkeypatch.setattr(broker, "publish", _publisher(VisualPhaseResult(status="failed", error="boom"), sent))
    await _post_entry(client, headers, username, pid, "2026-08-15")
    # до v2 visual_phase не присылал phase_probs — такие записи не годятся
    monkeypatch.setattr(broker, "publish", _publisher(
        VisualPhaseResult(status="done", phase_name="Earthwork", confidence=0.5), sent
    ))
    await _post_entry(client, headers, username, pid, "2026-08-20")
    monkeypatch.setattr(broker, "publish", _publisher(
        VisualPhaseResult(status="done", phase_name="Foundation", confidence=0.7, phase_probs=FOUNDATION), sent
    ))
    await _post_entry(client, headers, username, pid, "2026-09-01")

    assert [v.date for v in sent[-1].visual_history] == [date(2026, 8, 1), date(2026, 9, 1)]
    # запись от 15.08 видит только то, что было до неё
    assert [v.date for v in sent[1].visual_history] == [date(2026, 8, 1)]


async def test_failed_visual_phase_does_not_block_the_entry(client, auth, monkeypatch):
    username, headers = await auth()
    pid = await _project(client, headers)
    sent: list[PhaseCommand] = []
    monkeypatch.setattr(broker, "publish", _publisher(VisualPhaseResult(status="failed", error="boom"), sent))

    await _post_entry(client, headers, username, pid)

    assert len(sent) == 1 and sent[0].visual_history == []
    async with analysis.SessionLocal() as db:
        (entry,) = await _entries(db)
        assert json.loads(entry.visual_phase_details)["status"] == "failed"
        assert (entry.visual_phase_name, entry.analysis_status) == (None, "ready")


async def test_silent_visual_phase_times_out(client, auth, monkeypatch):
    """visual_phase молчит: phase.command ждёт, пока таймер не поставит
    маркер timeout. Сам фоновый таймер в тестовом ASGI-транспорте не получает
    управления до конца теста, поэтому вызываем то, что он вызывает."""
    username, headers = await auth()
    pid = await _project(client, headers)
    sent: list[PhaseCommand] = []
    monkeypatch.setattr(broker, "publish", _publisher(None, sent))

    await _post_entry(client, headers, username, pid)
    assert sent == []  # ждёт visual_phase

    async with analysis.SessionLocal() as db:
        (entry,) = await _entries(db)
    await analysis._visual_phase_timeout(entry.id, delay_s=0)

    assert len(sent) == 1 and sent[0].visual_history == []
    async with analysis.SessionLocal() as db:
        (entry,) = await _entries(db)
        assert json.loads(entry.visual_phase_details) == {"status": "timeout"}
        assert entry.analysis_status == "ready"


async def test_timeout_is_a_noop_once_the_visual_result_arrived(client, auth, monkeypatch):
    username, headers = await auth()
    pid = await _project(client, headers)
    sent: list[PhaseCommand] = []
    visual = VisualPhaseResult(status="done", phase_name="Foundation", confidence=0.7, phase_probs=FOUNDATION)
    monkeypatch.setattr(broker, "publish", _publisher(visual, sent))
    await _post_entry(client, headers, username, pid)

    async with analysis.SessionLocal() as db:
        (entry,) = await _entries(db)
    await analysis._visual_phase_timeout(entry.id, delay_s=0)

    assert len(sent) == 1
    async with analysis.SessionLocal() as db:
        (entry,) = await _entries(db)
        assert json.loads(entry.visual_phase_details)["status"] == "done"


async def test_entry_phase_is_the_phase_service_answer(client, auth, monkeypatch):
    username, headers = await auth()
    pid = await _project(client, headers)
    sent: list[PhaseCommand] = []
    visual = VisualPhaseResult(status="done", phase_name="Foundation", confidence=0.7, phase_probs=FOUNDATION)
    phase = PhaseResult(
        status="done",
        phase_name="Foundation",
        confidence=0.71,
        equipment_phase="Earthwork",
        visual_phase="Foundation",
        days_since_last_observation=0,
    )
    monkeypatch.setattr(broker, "publish", _publisher(visual, sent, phase))

    await _post_entry(client, headers, username, pid)

    async with analysis.SessionLocal() as db:
        (entry,) = await _entries(db)
        assert (entry.phase_name, entry.phase_confidence) == ("Foundation", 0.71)
        assert "По технике: Earthwork, по изображению: Foundation." in entry.stage_summary
        assert "Сигналы расходятся" in entry.stage_summary


def test_summary_warns_about_a_stale_estimate():
    stale = PhaseResult(status="done", phase_name="Foundation", confidence=0.8, days_since_last_observation=107)
    assert "Последнее наблюдение — 107 дн. назад." in analysis._summarize_phase(stale)
    fresh = PhaseResult(status="done", phase_name="Foundation", confidence=0.8, days_since_last_observation=3)
    assert "Последнее наблюдение" not in analysis._summarize_phase(fresh)


def test_summary_of_an_older_phase_service_is_unchanged():
    old = PhaseResult(status="done", phase_name="Earthwork", confidence=0.9)
    assert analysis._summarize_phase(old) == "Текущая фаза объекта: Earthwork (уверенность 90%)."
