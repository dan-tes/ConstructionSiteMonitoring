import hashlib
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import analysis
from config import settings
from database import Base, get_db
from main import app


@pytest_asyncio.fixture
async def client(tmp_path, monkeypatch) -> AsyncGenerator[AsyncClient, None]:
    engine = create_async_engine("sqlite+aiosqlite://", connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    testing_session = async_sessionmaker(engine, expire_on_commit=False)

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with testing_session() as session:
            yield session

    # Uploads go to a throwaway dir; the analysis pipeline runs synchronously
    # (no delay) so background tasks finish before the request returns.
    monkeypatch.setattr(settings, "media_root", tmp_path / "media")
    monkeypatch.setattr(settings, "analysis_delay_seconds", 0.0)
    # analysis.run_analysis opens its own SessionLocal(); point it at the test DB.
    monkeypatch.setattr(analysis, "SessionLocal", testing_session)

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
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
