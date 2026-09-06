VIDEO = ("clip.mp4", b"\x00\x00\x00 ftypisom" + b"\x00" * 64, "video/mp4")


async def _project(client, headers) -> str:
    return (
        await client.post("/projects", headers=headers, json={"name": "Объект"})
    ).json()["id"]


async def test_plan_upload_download_delete(client, auth):
    _, headers = await auth()
    pid = await _project(client, headers)

    up = await client.post(
        f"/projects/{pid}/plan",
        headers=headers,
        files=[("file", ("plan.pdf", b"%PDF-1.4 fake plan", "application/pdf"))],
    )
    assert up.status_code == 200
    plan = up.json()
    assert plan["kind"] == "other" or plan["type"] == "application/pdf"

    project = (await client.get(f"/projects/{pid}", headers=headers)).json()
    assert project["plan"]["id"] == plan["id"]

    served = await client.get(plan["url"].replace("http://localhost:8000", ""))
    assert served.status_code == 200
    assert served.content == b"%PDF-1.4 fake plan"

    assert (await client.delete(f"/projects/{pid}/plan", headers=headers)).status_code == 204
    assert (await client.get(f"/projects/{pid}", headers=headers)).json()["plan"] is None


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

    # analysis_delay_seconds is 0 in tests, so the background task has completed.
    got = (await client.get(f"/projects/{pid}/videos/{video_id}", headers=headers)).json()
    assert got["insight"]["status"] == "ready"
    assert got["insight"]["stageSummary"]
    assert got["insight"]["equipmentSummary"]
    assert got["insight"]["photos"] == []

    project = (await client.get(f"/projects/{pid}", headers=headers)).json()
    assert project["planStatus"] == got["insight"]["stageSummary"]

    served = await client.get(f"/files/{video_id}")
    assert served.status_code == 200


async def test_entry_rejects_non_video(client, auth):
    username, headers = await auth()
    pid = await _project(client, headers)
    bad = await client.post(
        f"/projects/{pid}/entries",
        headers=headers,
        data={"author": username, "date": "2026-09-01"},
        files=[("files", ("photo.jpg", b"\xff\xd8\xff", "image/jpeg"))],
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
