"""Sessions must survive a daemon restart: a brand-new SessionManager calling
load_persisted() should rehydrate a finished agent with its folder_id and
full message history intact."""
from __future__ import annotations

from fake_openai import text_turn


async def test_agent_survives_manager_restart(make_manager):
    mgr1, client1 = make_manager(script=[text_turn("all done")])
    folder = mgr1.folder_create(name="Persisted", workspace=None, isolation="shared")
    fid = folder["id"]

    s1 = mgr1.create(task="finish this", name="worker-1", folder=fid)
    await s1._task
    assert s1.status == "awaiting_input"
    sid = s1.id
    original_messages = list(s1.messages)

    # Simulate a daemon restart: a fresh manager, same on-disk home.
    mgr2, client2 = make_manager(script=[])
    mgr2.load_persisted()

    assert fid in {f["id"] for f in mgr2.folder_list()}  # folders.json also persisted
    assert sid in mgr2.sessions
    s2 = mgr2.sessions[sid]
    assert s2.folder_id == fid
    assert s2.name == "worker-1"
    assert s2.messages == original_messages
    # never resumed automatically, and never touched the (new) fake client
    assert client2.call_count == 0
