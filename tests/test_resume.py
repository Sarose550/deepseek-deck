"""An agent that reached awaiting_input can be resumed with send(): it appends
a user message, runs another cycle (status goes running -> awaiting_input
again), and the full message history is preserved across both turns."""
from __future__ import annotations

from fake_openai import text_turn


async def test_send_resumes_and_preserves_history(make_manager):
    mgr, client = make_manager(script=[
        text_turn("first reply"),
        text_turn("second reply"),
    ])

    s = mgr.create(task="start the work")
    await s._task
    assert s.status == "awaiting_input"
    assert s.final_message == "first reply"
    turns_after_first = s.turns_used

    ok = s.send("more")
    assert ok is True

    await s._task  # resumed cycle
    assert s.status == "awaiting_input"
    assert s.final_message == "second reply"
    assert s.turns_used == turns_after_first + 1
    assert client.call_count == 2

    roles_and_text = [
        (m["role"], m.get("content"))
        for m in s.messages if m["role"] != "system"
    ]
    assert roles_and_text == [
        ("user", "start the work"),
        ("assistant", "first reply"),
        ("user", "more"),
        ("assistant", "second reply"),
    ]


async def test_send_rejected_while_running(make_manager):
    mgr, client = make_manager(blocking=True)
    s = mgr.create(task="slow task")
    import asyncio
    for _ in range(50):
        if s.status == "running":
            break
        await asyncio.sleep(0.01)
    assert s.send("interrupt") is False  # can't send while a turn is in flight
    await s.stop()
