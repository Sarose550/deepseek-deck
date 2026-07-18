"""Regression guard: creating an agent with a given folder must assign that
folder's id, and the session_created event broadcast at spawn time must carry
the correct folder_id (this was recently broken and fixed)."""
from __future__ import annotations

from fake_openai import text_turn


async def test_spawn_assigns_and_announces_correct_folder(make_manager):
    mgr, client = make_manager(script=[text_turn("hello from the fake worker")])

    folder = mgr.folder_create(name="Project A", workspace=None, isolation="shared")
    fid = folder["id"]

    q = mgr.subscribe()
    s = mgr.create(task="do the thing", folder=fid)

    # 1. the session object itself has the right folder_id
    assert s.folder_id == fid

    # 2. the session_created event broadcast during create() carries it too
    events = []
    while not q.empty():
        events.append(q.get_nowait())
    created = [e for e in events
               if e.get("type") == "session_created" and e.get("session_id") == s.id]
    assert created, "expected a session_created event for the new session"
    assert created[0]["folder_id"] == fid

    await s._task  # drain the single scripted turn so the test exits clean
    assert s.status == "awaiting_input"
    assert client.call_count == 1


async def test_spawn_into_unfiled_by_default(make_manager):
    mgr, client = make_manager(script=[text_turn("hi")])
    s = mgr.create(task="no folder given")
    assert s.folder_id == "unfiled"
    await s._task
