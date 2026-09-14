async def test_project_crud_and_journal(client, auth):
    username, headers = await auth()

    assert (await client.get("/projects", headers=headers)).json() == []

    created = await client.post(
        "/projects",
        headers=headers,
        json={"name": "ЖК «Северный»", "description": "корпус 2"},
    )
    assert created.status_code == 201
    project = created.json()
    assert project["plan"] is None
    assert project["entries"] == []
    pid = project["id"]

    patched = await client.patch(
        f"/projects/{pid}", headers=headers, json={"description": "корпус 2, 12 этажей"}
    )
    assert patched.json()["description"] == "корпус 2, 12 этажей"
    assert patched.json()["name"] == "ЖК «Северный»"

    entry = await client.post(
        f"/projects/{pid}/entries",
        headers=headers,
        data={"author": username, "date": "2026-09-01"},
        files=[("files", ("site.mp4", b"\x00\x00\x00 ftypmp42fake", "video/mp4"))],
    )
    assert entry.status_code == 201
    assert len(entry.json()["media"]) == 1
    assert entry.json()["media"][0]["kind"] == "video"

    full = (await client.get(f"/projects/{pid}", headers=headers)).json()
    assert len(full["entries"]) == 1
    assert len(full["entries"][0]["media"]) == 1

    assert (await client.get("/projects", headers=headers)).json()[0]["id"] == pid

    deleted = await client.delete(f"/projects/{pid}", headers=headers)
    assert deleted.status_code == 204
    assert (await client.get(f"/projects/{pid}", headers=headers)).status_code == 404


async def test_projects_are_scoped_to_owner(client, auth):
    _, alice = await auth("alice")
    _, bob = await auth("bob")

    pid = (
        await client.post("/projects", headers=alice, json={"name": "Объект А"})
    ).json()["id"]

    assert (await client.get(f"/projects/{pid}", headers=bob)).status_code == 404
    assert (await client.get("/projects", headers=bob)).json() == []
    assert (
        await client.patch(f"/projects/{pid}", headers=bob, json={"name": "hijack"})
    ).status_code == 404


async def test_create_project_validation(client, auth):
    _, headers = await auth()
    assert (
        await client.post("/projects", headers=headers, json={"name": ""})
    ).status_code == 422
