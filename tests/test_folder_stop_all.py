"""Folder-level 'stop all': stopping a folder must stop every running agent in
it without deleting anything (conversations remain, agents stay in
manager.sessions)."""
from __future__ import annotations

import asyncio


async def test_folder_stop_stops_both_running_agents(make_manager):
    mgr, client = make_manager(blocking=True)  # both agents' create() hangs

    folder = mgr.folder_create(name="Batch", workspace=None, isolation="shared")
    fid = folder["id"]

    s1 = mgr.create(task="task one", folder=fid)
    s2 = mgr.create(task="task two", folder=fid)

    for _ in range(50):
        if s1.status == "running" and s2.status == "running" and client.call_count >= 2:
            break
        await asyncio.sleep(0.01)
    assert s1.status == "running"
    assert s2.status == "running"

    await mgr.folder_stop(fid)

    assert s1.status == "stopped"
    assert s2.status == "stopped"
    # still present and resumable — folder_stop must not remove anything
    assert s1.id in mgr.sessions
    assert s2.id in mgr.sessions


async def test_folder_stop_ignores_agents_in_other_folders(make_manager):
    mgr, client = make_manager(blocking=True)
    fa = mgr.folder_create(name="A", workspace=None, isolation="shared")["id"]
    fb = mgr.folder_create(name="B", workspace=None, isolation="shared")["id"]

    sa = mgr.create(task="in A", folder=fa)
    sb = mgr.create(task="in B", folder=fb)

    for _ in range(50):
        if sa.status == "running" and sb.status == "running":
            break
        await asyncio.sleep(0.01)

    await mgr.folder_stop(fa)

    assert sa.status == "stopped"
    assert sb.status == "running"  # untouched

    await sb.stop()  # cleanup so the test doesn't leak a running task
