"""A fake, offline stand-in for `openai.AsyncOpenAI`.

Mimics just the shape AgentSession._one_turn() reads from a streaming
`chat.completions.create(...)` call:

    chunk.choices[0].delta.content
    chunk.choices[0].delta.tool_calls[i].{index,id,function.name,function.arguments}
    chunk.choices[0].delta.reasoning_content   (optional)
    chunk.usage.{prompt_tokens,completion_tokens}   (on the final chunk)

Never touches the network. Every test in this suite must use this instead of
the real DeepSeek API.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Optional


def _delta_chunk(content=None, tool_calls=None, reasoning=None):
    delta = SimpleNamespace(content=content, tool_calls=None, reasoning_content=reasoning)
    if tool_calls:
        delta.tool_calls = [
            SimpleNamespace(index=idx, id=cid,
                             function=SimpleNamespace(name=name, arguments=args))
            for (idx, cid, name, args) in tool_calls
        ]
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)], usage=None)


def _usage_chunk(prompt_tokens=10, completion_tokens=5):
    return SimpleNamespace(
        choices=[],
        usage=SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens),
    )


def text_turn(text: str, prompt_tokens: int = 10, completion_tokens: int = 5) -> list:
    """One assistant turn that finishes the response (no tool calls)."""
    return [_delta_chunk(content=text), _usage_chunk(prompt_tokens, completion_tokens)]


def tool_call_turn(name: str, arguments_json: str, call_id: str = "call_1",
                    prompt_tokens: int = 10, completion_tokens: int = 5) -> list:
    """One assistant turn that emits a single tool call (loop continues)."""
    return [
        _delta_chunk(tool_calls=[(0, call_id, name, arguments_json)]),
        _usage_chunk(prompt_tokens, completion_tokens),
    ]


class FakeStream:
    """Async-iterable over a pre-built list of chunks."""

    def __init__(self, chunks: list):
        self._chunks = chunks

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for c in self._chunks:
            yield c


class FakeCompletions:
    def __init__(self, script: Optional[list] = None, blocking: bool = False):
        """`script` is a list of "turns" (each a list of chunks, see text_turn /
        tool_call_turn above), consumed one per `create()` call. `blocking=True`
        makes `create()` hang forever (until the test cancels the caller's
        task), modeling a worker mid-stream when `stop()` is called."""
        self.call_count = 0
        self.calls: list[dict] = []
        self._script = list(script or [])
        self._blocking = blocking
        self._never = asyncio.Event()

    async def create(self, **kwargs):
        self.call_count += 1
        self.calls.append(kwargs)
        if self._blocking:
            await self._never.wait()  # never set: models an in-flight call
        if self._script:
            chunks = self._script.pop(0)
        else:
            chunks = text_turn("(fake client: no more scripted turns)")
        return FakeStream(chunks)


class FakeClient:
    """Drop-in for `openai.AsyncOpenAI` — only the `.chat.completions.create`
    surface AgentSession touches."""

    def __init__(self, script: Optional[list] = None, blocking: bool = False):
        self.completions = FakeCompletions(script=script, blocking=blocking)
        self.chat = SimpleNamespace(completions=self.completions)

    @property
    def call_count(self) -> int:
        return self.completions.call_count
