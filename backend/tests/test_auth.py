import hashlib


async def test_salt_is_stable_and_hides_unknown_users(client):
    first = (await client.post("/auth/salt", json={"username": "ghost"})).json()["salt"]
    second = (await client.post("/auth/salt", json={"username": "ghost"})).json()["salt"]
    assert first == second
    assert len(first) == 32


async def test_register_login_session_logout(client):
    salt = (await client.post("/auth/salt", json={"username": "prorab"})).json()["salt"]
    ph = hashlib.sha256((salt + "secret123").encode()).hexdigest()

    reg = await client.post(
        "/auth/register", json={"username": "prorab", "passwordHash": ph, "salt": salt}
    )
    assert reg.status_code == 201
    body = reg.json()
    assert body["user"]["username"] == "prorab"
    assert set(body) == {"token", "user", "expiresAt"}

    # salt now returns the stored one
    assert (await client.post("/auth/salt", json={"username": "prorab"})).json()["salt"] == salt

    dup = await client.post(
        "/auth/register", json={"username": "prorab", "passwordHash": ph, "salt": salt}
    )
    assert dup.status_code == 409
    assert dup.json()["detail"]["code"] == "USERNAME_TAKEN"

    bad = await client.post("/auth/login", json={"username": "prorab", "passwordHash": "0" * 64})
    assert bad.status_code == 401
    assert bad.json()["detail"]["code"] == "INVALID_CREDENTIALS"

    ok = await client.post("/auth/login", json={"username": "prorab", "passwordHash": ph})
    assert ok.status_code == 200
    token = ok.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    assert (await client.get("/auth/session", headers=headers)).status_code == 200
    assert (await client.post("/auth/logout", headers=headers)).status_code == 204
    assert (await client.get("/auth/session", headers=headers)).status_code == 401


async def test_register_rejects_short_username(client):
    salt = (await client.post("/auth/salt", json={"username": "ab"})).json()["salt"]
    ph = hashlib.sha256((salt + "secret123").encode()).hexdigest()
    resp = await client.post(
        "/auth/register", json={"username": "ab", "passwordHash": ph, "salt": salt}
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "VALIDATION"


async def test_protected_route_requires_token(client):
    assert (await client.get("/projects")).status_code == 401
    assert (
        await client.get("/projects", headers={"Authorization": "Bearer nope"})
    ).status_code == 401
