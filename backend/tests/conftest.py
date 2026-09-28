import hashlib
import json
import uuid
from collections.abc import AsyncGenerator
from datetime import date, datetime, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import analysis
from config import settings
from database import Base, get_db
from integrations import broker
from integrations.schemas import (
    DelayForecastResult,
    DetectResult,
    Envelope,
    EquipmentCount,
    NormalizedPhase,
    PhaseResult,
    PlanNormalizeResult,
    VisualPhaseResult,
)
from main import app


async def _fake_publish(routing_key: str, body: bytes) -> None:
    """Stands in for RabbitMQ + the planner/vision/phase/delay services in
    tests: routes a command straight to a canned result and feeds it back
    into the matching analysis.handle_*_result, in-process. Keeps the
    pipeline's orchestration logic (analysis.py) under test without a
    running broker or workers — see integrations/README.md for the real
    topology this mocks."""
    correlation_id = uuid.UUID(json.loads(body)["correlation_id"])

    if routing_key == broker.PLAN_COMMAND:
        result = PlanNormalizeResult(
            status="done",
            phases=[
                NormalizedPhase(
                    phase="Earthwork",
                    planned_duration_days=30,
                    expected_equipment=["worker", "excavator"],
                )
            ],
            project_duration_days=120,
        )
        await analysis.handle_plan_result(
            Envelope(
                correlation_id=correlation_id,
                published_at=datetime.now(timezone.utc),
                payload=result,
            )
        )
    elif routing_key == broker.VISION_COMMAND:
        result = DetectResult(
            status="done",
            counts=[EquipmentCount(equipment_class="excavator", count=1)],
        )
        await analysis.handle_vision_result(
            Envelope(
                correlation_id=correlation_id,
                published_at=datetime.now(timezone.utc),
                payload=result,
            )
        )
    elif routing_key == broker.VISUAL_PHASE_COMMAND:
        result = VisualPhaseResult(status="done", phase_name="Earthwork", confidence=0.7, cluster_id=1)
        await analysis.handle_visual_phase_result(
            Envelope(
                correlation_id=correlation_id,
                published_at=datetime.now(timezone.utc),
                payload=result,
            )
        )
    elif routing_key == broker.PHASE_COMMAND:
        result = PhaseResult(status="done", phase_name="Earthwork", confidence=0.9)
        await analysis.handle_phase_result(
            Envelope(
                correlation_id=correlation_id,
                published_at=datetime.now(timezone.utc),
                payload=result,
            )
        )
    elif routing_key == broker.DELAY_COMMAND:
        result = DelayForecastResult(
            status="done",
            delay_days=2,
            expected_completion=date.today(),
            confidence=0.8,
            spi_time=0.95,
        )
        await analysis.handle_delay_result(
            Envelope(
                correlation_id=correlation_id,
                published_at=datetime.now(timezone.utc),
                payload=result,
            )
        )
    else:
        raise AssertionError(f"unexpected routing key in test: {routing_key}")


@pytest_asyncio.fixture
async def client(tmp_path, monkeypatch) -> AsyncGenerator[AsyncClient, None]:
    engine = create_async_engine("sqlite+aiosqlite://", connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    testing_session = async_sessionmaker(engine, expire_on_commit=False)

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with testing_session() as session:
            yield session

    # Uploads go to a throwaway dir.
    monkeypatch.setattr(settings, "media_root", tmp_path / "media")
    # analysis.run_analysis opens its own SessionLocal(); point it at the test DB.
    monkeypatch.setattr(analysis, "SessionLocal", testing_session)
    # No RabbitMQ (or vision/phase/delay services) in tests — _fake_publish
    # drives the same handle_*_result chain in-process, synchronously, so the
    # background task is still fully done by the time the request returns.
    monkeypatch.setattr(broker, "publish", _fake_publish)

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    # run_entry_analysis заводит фоновый таймер ожидания visual_phase — в
    # тестах visual.result приходит сразу, так что он не нужен; гасим.
    for task in list(analysis._background_tasks):
        task.cancel()
    app.dependency_overrides.clear()
    await engine.dispose()


@pytest_asyncio.fixture
async def auth(client: AsyncClient):
    """Registers a user and returns (username, auth headers)."""

    async def _make(username: str = "prorab_ivan", password: str = "hunter2pw") -> tuple[str, dict]:
        salt = (await client.post("/auth/salt", json={"username": username})).json()["salt"]
        password_hash = hashlib.sha256((salt + password).encode()).hexdigest()
        resp = await client.post(
            "/auth/register",
            json={"username": username, "passwordHash": password_hash, "salt": salt},
        )
        token = resp.json()["token"]
        return username, {"Authorization": f"Bearer {token}"}

    return _make
