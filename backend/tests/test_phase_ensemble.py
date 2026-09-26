import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import select

import analysis
import phase_ensemble
from integrations import broker
from integrations.schemas import CANONICAL_PHASES, Envelope, PhaseResult, VisualPhaseResult
from models import JournalEntry
from tests.conftest import _fake_publish
from tests.test_media import VIDEO, _project


def _dist(**weights: float) -> dict[str, float]:
    """{фаза: вес} по именам с подчёркиваниями вместо пробелов, остальным фазам — 0.01."""
    named = {k.replace("_", " "): v for k, v in weights.items()}
    return {p: named.get(p, 0.01) for p in CANONICAL_PHASES}


def test_visual_signal_breaks_a_close_equipment_call():
    # по технике кладка и отделка почти вровень — снимок решает
    equipment = _dist(Masonry=0.45, Finishing=0.40)
    visual = _dist(Finishing=0.9)
    phase, _ = phase_ensemble.top(phase_ensemble.fuse(equipment, visual))
    assert phase == "Finishing"


def test_confident_equipment_call_survives_a_weak_visual_disagreement():
    equipment = _dist(Foundation=0.9)
    visual = _dist(Earthwork=0.4, Foundation=0.3)
    assert phase_ensemble.top(phase_ensemble.fuse(equipment, visual))[0] == "Foundation"


def test_phase_unknown_to_visual_is_neutral_not_forbidden():
    # visual_phase не знает Preconstruction — это не должно её «запрещать»
    visual = {p: 1 / 9 for p in CANONICAL_PHASES if p != "Preconstruction"}
    equipment = _dist(Preconstruction=0.8)
    assert phase_ensemble.top(phase_ensemble.fuse(equipment, visual))[0] == "Preconstruction"


def test_fuse_without_visual_is_equipment_alone():
    equipment = _dist(Earthwork=0.7)
    fused = phase_ensemble.fuse(equipment, None)
    total = sum(equipment.values())
    assert abs(fused["Earthwork"] - 0.7 / total) < 1e-4


def test_point_estimate_fallback_for_workers_without_probs():
    dist = phase_ensemble.from_point_estimate("Foundation", 0.8)
    assert phase_ensemble.top(dist) == ("Foundation", 0.8)
    assert abs(sum(dist.values()) - 1) < 1e-9
    assert phase_ensemble.from_point_estimate("not a phase", 0.8) is None


def _publisher(phase: PhaseResult, visual: VisualPhaseResult | None):
    """_fake_publish, но с заданными ответами phase/visual_phase; visual=None —
    visual_phase не отвечает вовсе."""

    async def publish(routing_key: str, body: bytes) -> None:
        correlation_id = uuid.UUID(json.loads(body)["correlation_id"])
        now = datetime.now(timezone.utc)
        if routing_key == broker.PHASE_COMMAND:
            await analysis.handle_phase_result(Envelope(correlation_id=correlation_id, published_at=now, payload=phase))
        elif routing_key == broker.VISUAL_PHASE_COMMAND:
            if visual is not None:
                await analysis.handle_visual_phase_result(
                    Envelope(correlation_id=correlation_id, published_at=now, payload=visual)
                )
        else:
            await _fake_publish(routing_key, body)

    return publish


async def _entry_phase(client, auth, monkeypatch, phase, visual) -> str | None:
    username, headers = await auth()
    pid = await _project(client, headers)
    monkeypatch.setattr(broker, "publish", _publisher(phase, visual))
    await client.post(
        f"/projects/{pid}/entries",
        headers=headers,
        data={"author": username, "date": "2026-09-01"},
        files=[("files", VIDEO)],
    )
    points = (await client.get(f"/projects/{pid}/timeline", headers=headers)).json()["points"]
    return points[0]["phase"] if points else None


async def test_entry_phase_is_the_ensemble_not_the_raw_equipment_answer(client, auth, monkeypatch):
    phase = PhaseResult(
        status="done", phase_name="Masonry", confidence=0.45, probs=_dist(Masonry=0.45, Finishing=0.40)
    )
    visual = VisualPhaseResult(status="done", phase_name="Finishing", confidence=0.9, probs=_dist(Finishing=0.9))
    assert await _entry_phase(client, auth, monkeypatch, phase, visual) == "Finishing"


async def test_failed_visual_phase_falls_back_to_equipment(client, auth, monkeypatch):
    phase = PhaseResult(status="done", phase_name="Masonry", confidence=0.45, probs=_dist(Masonry=0.45, Finishing=0.40))
    visual = VisualPhaseResult(status="failed", error="boom")
    assert await _entry_phase(client, auth, monkeypatch, phase, visual) == "Masonry"


async def test_silent_visual_phase_times_out_to_equipment(client, auth, monkeypatch):
    """visual_phase молчит: фаза записи не должна зависнуть — по истечении
    ожидания она берётся по одной технике. Сам фоновый таймер (sleep +
    вызов) в тестовом ASGI-транспорте не получает управления до конца
    теста, поэтому проверяем то, что он вызывает, — _finalize_after_wait."""
    monkeypatch.setattr(analysis, "VISUAL_PHASE_WAIT_SECONDS", 0.0)
    phase = PhaseResult(status="done", phase_name="Masonry", confidence=0.45, probs=_dist(Masonry=0.45, Finishing=0.40))
    username, headers = await auth()
    pid = await _project(client, headers)
    monkeypatch.setattr(broker, "publish", _publisher(phase, None))
    await client.post(
        f"/projects/{pid}/entries",
        headers=headers,
        data={"author": username, "date": "2026-09-01"},
        files=[("files", VIDEO)],
    )
    assert (await client.get(f"/projects/{pid}/timeline", headers=headers)).json()["points"] == []  # ждёт visual

    async with analysis.SessionLocal() as db:
        entry_id = (await db.execute(select(JournalEntry.id))).scalar_one()
    await analysis._finalize_after_wait(entry_id)

    points = (await client.get(f"/projects/{pid}/timeline", headers=headers)).json()["points"]
    assert [p["phase"] for p in points] == ["Masonry"]
    async with analysis.SessionLocal() as db:
        assert (await db.get(JournalEntry, entry_id)).visual_phase_status == "timeout"


async def test_wait_timer_fires_for_consumer_delivered_results(client, auth, monkeypatch):
    """Как в проде: phase.result приходит из consumer'а брокера (не изнутри
    HTTP-запроса) — тогда фоновый таймер ожидания visual_phase срабатывает сам."""
    monkeypatch.setattr(analysis, "VISUAL_PHASE_WAIT_SECONDS", 0.01)
    username, headers = await auth()
    pid = await _project(client, headers)

    async def publish(routing_key: str, body: bytes) -> None:
        if routing_key in (broker.PHASE_COMMAND, broker.VISUAL_PHASE_COMMAND):
            return  # ответы «придут позже» из consumer'а
        await _fake_publish(routing_key, body)

    monkeypatch.setattr(broker, "publish", publish)
    await client.post(
        f"/projects/{pid}/entries",
        headers=headers,
        data={"author": username, "date": "2026-09-01"},
        files=[("files", VIDEO)],
    )
    async with analysis.SessionLocal() as db:
        entry_id = (await db.execute(select(JournalEntry.id))).scalar_one()

    phase = PhaseResult(status="done", phase_name="Masonry", confidence=0.45, probs=_dist(Masonry=0.45, Finishing=0.40))
    await analysis.handle_phase_result(
        Envelope(correlation_id=entry_id, published_at=datetime.now(timezone.utc), payload=phase)
    )
    for task in list(analysis._background_tasks):
        await task

    async with analysis.SessionLocal() as db:
        entry = await db.get(JournalEntry, entry_id)
        assert (entry.visual_phase_status, entry.phase_name, entry.analysis_status) == ("timeout", "Masonry", "ready")
