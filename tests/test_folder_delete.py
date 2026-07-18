"""Deleting a folder must stop and remove every agent inside it — they should
be gone from manager.sessions afterward."""
from __future__ import annotations

import asyncio


async def test_folder_delete_removes_its_agents(make_manager):
    mgr, client = make_manager(blocking=True)
    folder = mgr.folder_create(name="Doomed", workspace=None, isolation="shared")
    fid = folder["id"]

    s1 = mgr.create(task="one", folder=fid)
    s2 = mgr.create(task="two", folder=fid)
    other = mgr.folder_create(name="Keep", workspace=None, isolation="shared")
    s3 = mgr.create(task="three", folder=other["id"])

    for _ in range(50):
        if all(s.status == "running" for s in (s1, s2, s3)):
            break
        await asyncio.sleep(0.01)

    ok = await mgr.folder_delete(fid)
    assert ok

    assert s1.id not in mgr.sessions
    assert s2.id not in mgr.sessions
    assert s3.id in mgr.sessions  # untouched, different folder
    assert fid not in {f["id"] for f in mgr.folder_list()}

    await s3.stop()  # cleanup
