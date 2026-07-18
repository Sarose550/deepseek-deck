"""Stopping a running agent must actually halt its loop: status -> stopped,
the underlying asyncio task is cancelled/done, and no further API calls
happen after stop() returns."""
from __future__ import annotations

import asyncio


async def test_stop_halts_a_running_agent(make_manager):
    mgr, client = make_manager(blocking=True)  # create() hangs forever
    s = mgr.create(task="do something slow")

    # Let the event loop run the task up to its blocking `create()` await.
    for _ in range(50):
        if s.status == "running" and client.call_count >= 1:
            break
        await asyncio.sleep(0.01)
    assert s.status == "running"
    assert client.call_count == 1

    await s.stop()

    assert s.status == "stopped"
    assert s._task.done()
    # give the loop one more tick — nothing should call the fake client again
    await asyncio.sleep(0.02)
    assert client.call_count == 1
