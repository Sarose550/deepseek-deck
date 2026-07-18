# DeepSeek Deck test suite

Fully offline: no test touches the real DeepSeek API or the network. Every
test that needs a `SessionManager` gets one wired to `FakeClient`
(`tests/fake_openai.py`), a small stand-in for `openai.AsyncOpenAI` that
returns scripted streaming chunks instead of calling out. Each test also gets
its own throwaway `DECK_HOME` (via the `isolated_home` fixture in
`conftest.py`), so nothing reads or writes your real `~/.deepseek-deck` state.

## Run

```bash
cd /Users/samrosenstrauch/Documents/deepseek-deck
.venv/bin/python -m pytest -q
```

Run a single file or test:

```bash
.venv/bin/python -m pytest -q tests/test_stop.py
.venv/bin/python -m pytest -q tests/test_resume.py::test_send_resumes_and_preserves_history
```

## Layout

- `fake_openai.py` — `FakeClient` / `FakeCompletions` / `FakeStream` and
  chunk-builder helpers (`text_turn`, `tool_call_turn`). Mimics the streaming
  shape `AgentSession._one_turn()` reads: `delta.content`, `delta.tool_calls`,
  `delta.reasoning_content`, and a final chunk carrying `.usage`.
- `conftest.py` — `isolated_home` (redirects `deck.config` + `deck.folders`
  on-disk paths into `tmp_path`) and `make_manager` (factory returning a
  `(SessionManager, FakeClient)` pair pointed at that isolated home).
- `test_spawn_folder.py` — spawning into a folder assigns the right
  `folder_id` and the `session_created` broadcast event carries it too
  (regression guard).
- `test_stop.py` — `stop()` on a running agent transitions it to `stopped`,
  finishes its task, and no further API calls happen afterward.
- `test_folder_stop_all.py` — `SessionManager.folder_stop()` stops every
  running agent in one folder and leaves other folders' agents alone.
- `test_folder_delete.py` — deleting a folder removes all its agents from
  `manager.sessions` (and leaves agents in other folders untouched).
- `test_resume.py` — `send()` on an `awaiting_input` agent resumes the loop
  and the full message history (both turns) is preserved.
- `test_persistence.py` — an agent survives a simulated daemon restart: a new
  `SessionManager.load_persisted()` rehydrates it with the right `folder_id`
  and message history.
- `test_worktree.py` — a folder with `isolation="worktree"` mounted on a real
  (locally created) git repo gives each agent its own `git worktree` on
  branch `deck/<id>` under `<repo>/.deck-worktrees/<id>`.

## Notes

- `SessionManager.__init__` accepts an optional `client=` parameter (defaults
  to a real `AsyncOpenAI` if omitted) purely so tests can inject `FakeClient`.
  This doesn't change production behavior — nothing in `server.py` or
  `cli.py` passes `client=`, so the daemon still always uses the real API.
- Tests that need a "still running" agent use `FakeClient(blocking=True)`,
  whose `create()` awaits an `asyncio.Event` that is never set — the call
  hangs exactly like an in-flight streaming request until the test cancels it
  (via `stop()`), which is what lets `test_stop.py` assert no call happens
  after `stop()` returns.
