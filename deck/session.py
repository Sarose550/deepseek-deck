"""Agent sessions: streaming, resumable, full-duplex DeepSeek workers.

Each AgentSession owns a message history and runs the DeepSeek agent loop as an
asyncio task, streaming events (reasoning/content deltas, tool calls, tool
results) to subscribers and persisting a durable transcript to disk. A session
that finishes a response goes to `awaiting_input` — you can `send()` it a
follow-up and it resumes, mirroring how a Claude Code subagent can be messaged
again.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

from openai import AsyncOpenAI

from . import config as cfg
from . import folders as _folders

cfg.ensure_tools_importable()
from deepseek_mcp.tools import build_tool_schemas, execute_tool  # noqa: E402

SENSITIVE_ARG_KEYS = {"content", "new_string"}
UI_RESULT_CAP = 6000

SYSTEM_PROMPT = """You are a DeepSeek worker agent running inside the DeepSeek Deck.

You are given a focused task to complete autonomously within your workspace.
You have local tools: {tools}

Rules:
1. Stay strictly within the workspace: {workspace}
2. Read before editing. Don't guess file contents.
3. For batch tasks (translating, extracting, refactoring many files), iterate file-by-file.
4. When done, return a final message summarizing:
   - What you did (file paths affected)
   - Any issues / files you couldn't process
   - A brief summary the supervisor can use without re-reading everything
5. Don't ask clarifying questions back. Make reasonable assumptions and document them.
6. If a tool returns "ERROR: ...", read it and decide: retry with fixed input, skip, or
   report and stop. Don't blindly loop on the same error.
"""


def _redact_args(args: dict) -> dict:
    out = {}
    for k, v in args.items():
        if k in SENSITIVE_ARG_KEYS and isinstance(v, str):
            out[k] = f"<{len(v)} chars>"
        elif isinstance(v, str) and len(v) > 200:
            out[k] = v[:200] + f"… <{len(v)} chars>"
        else:
            out[k] = v
    return out


class AgentSession:
    def __init__(
        self,
        manager: "SessionManager",
        sid: str,
        task: str,
        workspace: Path,
        model: str,
        max_turns: int,
        allowed_tools: list[str],
        name: Optional[str] = None,
        folder_id: str = "unfiled",
        worktree_repo: Optional[str] = None,
    ):
        self.manager = manager
        self.id = sid
        self.name = name or sid
        self.task = task
        self.workspace = workspace
        self.folder_id = folder_id
        self.worktree_repo = worktree_repo   # set if workspace is a git worktree
        self.model = model
        self.max_turns = max_turns
        self.allowed_tools = allowed_tools
        self.tool_schemas = build_tool_schemas(allowed_tools)

        self.messages: list[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT.format(
                tools=", ".join(allowed_tools), workspace=workspace)},
            {"role": "user", "content": task},
        ]
        self.status = "starting"
        self.final_message = ""
        self.turns_used = 0
        self.tool_calls = 0
        self.tokens = {"prompt": 0, "completion": 0, "total": 0}
        self.created_at = time.time()
        self.updated_at = self.created_at
        self.error: Optional[str] = None

        self._seq = 0
        self._task: Optional[asyncio.Task] = None
        self._dir = cfg.SESSIONS_DIR / sid
        self._dir.mkdir(parents=True, exist_ok=True)

    # --- persistence -------------------------------------------------------

    def meta(self, brief: bool = True) -> dict:
        m = {
            "id": self.id, "name": self.name, "status": self.status,
            "model": self.model, "workspace": str(self.workspace),
            "folder_id": self.folder_id,
            "turns_used": self.turns_used, "tool_calls": self.tool_calls,
            "tokens": self.tokens, "created_at": self.created_at,
            "updated_at": self.updated_at, "error": self.error,
        }
        if not brief:
            m["task"] = self.task
            m["final_message"] = self.final_message
            m["allowed_tools"] = self.allowed_tools
            m["max_turns"] = self.max_turns
            m["worktree_repo"] = self.worktree_repo
        return m

    def _persist_meta(self) -> None:
        try:
            (self._dir / "meta.json").write_text(
                json.dumps(self.meta(brief=False), ensure_ascii=False, indent=2),
                encoding="utf-8")
        except OSError:
            pass

    def _persist_messages(self) -> None:
        try:
            (self._dir / "messages.json").write_text(
                json.dumps(self.messages, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    # --- events ------------------------------------------------------------

    def _emit(self, etype: str, persist: bool = True, **payload) -> None:
        self._seq += 1
        self.updated_at = time.time()
        ev = {"seq": self._seq, "session_id": self.id, "ts": self.updated_at,
              "type": etype, **payload}
        if persist:
            try:
                with (self._dir / "events.jsonl").open("a", encoding="utf-8") as f:
                    f.write(json.dumps(ev, ensure_ascii=False) + "\n")
            except OSError:
                pass
        self.manager.broadcast(ev)

    def _set_status(self, status: str) -> None:
        self.status = status
        self._persist_meta()
        self._emit("status", status=status)

    # --- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        self._persist_meta()
        self._emit("session_created", name=self.name, task=self.task,
                   workspace=str(self.workspace), model=self.model,
                   folder_id=self.folder_id)
        self._task = asyncio.create_task(self._run_cycle())

    def _clean_orphaned_toolcalls(self) -> None:
        """Strip any trailing assistant message that has tool_calls but no
        matching tool-result messages after it.  Such orphaned messages can
        appear when an agent is stopped mid‑tool‑execution; the DeepSeek API
        rejects them on the next request."""
        while self.messages:
            last = self.messages[-1]
            if last.get("role") == "assistant" and last.get("tool_calls"):
                self.messages.pop()
            else:
                break

    def send(self, text: str) -> bool:
        """Append a follow-up user message and resume the loop."""
        if self.status == "running":
            return False
        self._clean_orphaned_toolcalls()
        self.messages.append({"role": "user", "content": text})
        self._persist_messages()
        self._emit("user_message", text=text)
        # Mark the parent folder as recently interacted with
        self.manager.folders.touch(self.folder_id)
        fobj = self.manager.folders.folders.get(self.folder_id)
        if fobj:
            self.manager.broadcast({"type": "folder_updated", **fobj.to_dict()})
        self._task = asyncio.create_task(self._run_cycle())
        return True

    def rewind(self, to_msg: int) -> int:
        """Truncate message history at the given user/assistant message boundary.
        Returns number of messages removed."""
        count = 0
        idx = 0
        for i, m in enumerate(self.messages):
            if m.get("role") in ("user", "assistant"):
                if count == to_msg:
                    idx = i
                    break
                count += 1
        else:
            return 0  # to_msg beyond end — nothing to rewind
        removed = len(self.messages) - idx
        if removed <= 0:
            return 0
        self.messages = self.messages[:idx]
        self._persist_messages()
        self._emit("rewound", to_msg=to_msg, removed=removed)
        return removed

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    async def _run_cycle(self) -> None:
        self._set_status("running")
        try:
            for _ in range(self.max_turns):
                finished = await self._one_turn()
                if finished:
                    self._set_status("awaiting_input")
                    self._emit("response_done", final_message=self.final_message,
                               turns_used=self.turns_used, tokens=self.tokens,
                               tool_calls=self.tool_calls)
                    return
            self.error = f"exceeded max_turns ({self.max_turns})"
            self._set_status("error")
            self._emit("error", message=self.error)
        except asyncio.CancelledError:
            self._set_status("stopped")
            raise
        except Exception as e:  # noqa: BLE001
            self.error = f"{type(e).__name__}: {e}"
            self._set_status("error")
            self._emit("error", message=self.error)

    async def _one_turn(self) -> bool:
        """Run one streamed assistant turn. Returns True if the response is done
        (no tool calls), False if tools ran and another turn is needed."""
        loop = asyncio.get_running_loop()
        self._emit("turn_start", turn=self.turns_used, persist=True)

        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        toolcalls: dict[int, dict] = {}

        async with self.manager.semaphore:
            stream = await self.manager.client.chat.completions.create(
                model=self.model,
                messages=self.messages,
                tools=self.tool_schemas or None,
                tool_choice="auto" if self.tool_schemas else None,
                stream=True,
                stream_options={"include_usage": True},
            )
            async for chunk in stream:
                usage = getattr(chunk, "usage", None)
                if usage:
                    self.tokens["prompt"] += usage.prompt_tokens or 0
                    self.tokens["completion"] += usage.completion_tokens or 0
                    self.tokens["total"] = self.tokens["prompt"] + self.tokens["completion"]
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta is None:
                    continue
                rc = getattr(delta, "reasoning_content", None)
                if rc is None and getattr(delta, "model_extra", None):
                    rc = delta.model_extra.get("reasoning_content")
                if rc:
                    reasoning_parts.append(rc)
                    self._emit("reasoning_delta", persist=False, text=rc)
                if delta.content:
                    content_parts.append(delta.content)
                    self._emit("content_delta", persist=False, text=delta.content)
                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        slot = toolcalls.setdefault(tc.index, {"id": "", "name": "", "args": ""})
                        if tc.id:
                            slot["id"] = tc.id
                        if tc.function:
                            if tc.function.name:
                                slot["name"] = tc.function.name
                            if tc.function.arguments:
                                slot["args"] += tc.function.arguments

        self.turns_used += 1
        content = "".join(content_parts)
        reasoning = "".join(reasoning_parts)

        amsg: dict[str, Any] = {"role": "assistant", "content": content or None}
        if reasoning:
            amsg["reasoning_content"] = reasoning
        if toolcalls:
            amsg["tool_calls"] = [
                {"id": s["id"] or f"call_{i}", "type": "function",
                 "function": {"name": s["name"], "arguments": s["args"]}}
                for i, s in sorted(toolcalls.items())
            ]
        self.messages.append(amsg)

        if not toolcalls:
            self._persist_messages()
            self.final_message = content or "(empty response)"
            return True

        for i, s in sorted(toolcalls.items()):
            self.tool_calls += 1
            tcid = s["id"] or f"call_{i}"
            name = s["name"]
            try:
                args = json.loads(s["args"]) if s["args"] else {}
            except json.JSONDecodeError as e:
                result = f"ERROR: invalid JSON in tool arguments: {e}"
                self._emit("tool_call", tool_call_id=tcid, name=name, args={"_raw": s["args"][:200]})
            else:
                self._emit("tool_call", tool_call_id=tcid, name=name, args=_redact_args(args))
                result = await loop.run_in_executor(
                    None, execute_tool, name, args, self.workspace)
            ok = not result.startswith("ERROR:")
            self._emit("tool_result", tool_call_id=tcid, name=name, ok=ok,
                       result=result[:UI_RESULT_CAP])
            self.messages.append({"role": "tool", "tool_call_id": tcid, "content": result})

        self._persist_messages()
        return False


class SessionManager:
    def __init__(self, config: cfg.DeckConfig, client: Optional[Any] = None):
        """`client` lets tests inject a fake OpenAI-shaped async client instead
        of hitting the real DeepSeek API. Production always gets a real
        AsyncOpenAI since no caller passes `client`."""
        self.config = config
        self.client = client or AsyncOpenAI(api_key=config.api_key, base_url=config.base_url)
        self.semaphore = asyncio.Semaphore(cfg.MAX_CONCURRENCY)
        self.sessions: dict[str, AgentSession] = {}
        self._subscribers: set[asyncio.Queue] = set()
        self.folders = _folders.FolderStore()

    # --- folders ----------------------------------------------------------

    def folder_list(self) -> list[dict]:
        return self.folders.list()

    def folder_get(self, fid: str) -> Optional[dict]:
        f = self.folders.folders.get(fid)
        return f.to_dict() if f else None

    def folder_create(self, name: str, workspace: Optional[str] = None,
                      isolation: str = "shared", source: str = "manual") -> dict:
        f = self.folders.create(name, workspace, isolation, source)
        self.broadcast({"type": "folder_created", **f.to_dict()})
        return f.to_dict()

    def folder_rename(self, fid: str, name: str) -> bool:
        ok = self.folders.rename(fid, name)
        if ok:
            self.broadcast({"type": "folder_updated", **self.folders.folders[fid].to_dict()})
        return ok

    def folder_archive(self, fid: str, archived: bool) -> bool:
        ok = self.folders.set_archived(fid, archived)
        if ok:
            self.broadcast({"type": "folder_updated", **self.folders.folders[fid].to_dict()})
        return ok

    async def folder_stop(self, fid: str) -> int:
        """Stop every running/starting agent in a folder, without removing them
        or their conversations (they stay resumable via `send`)."""
        stopped = 0
        for s in [s for s in self.sessions.values()
                  if s.folder_id == fid and s.status in ("running", "starting")]:
            await s.stop()
            stopped += 1
        return stopped

    async def folder_delete(self, fid: str) -> bool:
        """Delete a folder and every agent inside it (stops running workers)."""
        if fid == _folders.UNFILED_ID or fid not in self.folders.folders:
            return False
        for sid in [s.id for s in self.sessions.values() if s.folder_id == fid]:
            await self.remove(sid)
        self.folders.delete(fid)
        self.broadcast({"type": "folder_deleted", "id": fid})
        return True

    def _resolve_workspace(self, folder: "_folders.Folder", sid: str,
                           override: Optional[str]) -> tuple[Path, Optional[str]]:
        """Return (workspace_path, worktree_repo). Per-agent override wins;
        else the folder's directory (shared or its own git worktree); else an
        isolated scratch dir."""
        if override:
            p = Path(override).expanduser().resolve()
            p.mkdir(parents=True, exist_ok=True)
            return p, None
        if folder.workspace:
            repo = Path(folder.workspace)
            if folder.isolation == "worktree" and _folders.is_git_repo(repo):
                wt = _folders.make_worktree(repo, sid)
                if wt is not None:
                    return wt, str(repo)
            return repo, None                      # shared (or worktree fallback)
        ws = cfg.DECK_HOME / "workspaces" / sid    # Unfiled → scratch
        ws.mkdir(parents=True, exist_ok=True)
        return ws, None

    # --- pub/sub for websockets -------------------------------------------

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def broadcast(self, event: dict) -> None:
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    # --- session ops -------------------------------------------------------

    def _new_id(self, name: Optional[str]) -> str:
        base = uuid.uuid4().hex[:6]
        return base

    async def _auto_name(self, task: str) -> Optional[str]:
        """Use deepseek-v4-flash to generate a short descriptive caption from the task."""
        models_to_try = ["deepseek-v4-flash", self.config.model]
        for model in models_to_try:
            try:
                async with self.semaphore:
                    stream = await self.client.chat.completions.create(
                        model=model,
                        messages=[
                            {"role": "system", "content": "You generate very short, descriptive titles (5 words or fewer) for AI worker tasks. Respond with ONLY the title — no quotes, no punctuation, no explanation."},
                            {"role": "user", "content": f"Task: {task[:500]}\n\nTitle:"},
                        ],
                        max_tokens=60,
                        temperature=0.3,
                        stream=True,
                    )
                    parts: list[str] = []
                    async for chunk in stream:
                        if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                            parts.append(chunk.choices[0].delta.content)
                    content = "".join(parts).strip()
                if not content:
                    continue
                title = content.strip().strip('"').strip("'")
                # sanitise: remove anything that isn't word-char, space, dash, or paren
                title = "".join(c for c in title if c.isalnum() or c in (" ", "-", "_", "(", ")")).strip()
                if title:
                    return title[:40]
            except Exception:  # noqa: BLE001 — try next model
                continue
        # Fallback: use first 3 meaningful words from the task
        words = [w for w in task.replace("\n", " ").split() if len(w) > 1 and w.isalpha()]
        if words:
            return " ".join(words[:5])[:40]
        return None

    async def create(self, task: str, name: Optional[str] = None,
               workspace: Optional[str] = None, model: Optional[str] = None,
               max_turns: Optional[int] = None,
               allowed_tools: Optional[list[str]] = None,
               folder: Optional[str] = None) -> AgentSession:
        sid = self._new_id(name)
        fobj = None
        if folder:
            fobj = self.folders.get(folder)
            if fobj is None:                       # auto-create by name
                fobj = self.folders.create(folder, workspace=workspace, source="manual")
                self.broadcast({"type": "folder_created", **fobj.to_dict()})
        if fobj is None:
            fobj = self.folders.folders[_folders.UNFILED_ID]
        # Auto-generate a name from the task if the user didn't provide one
        if not name:
            name = await self._auto_name(task)
        ws, worktree_repo = self._resolve_workspace(fobj, sid, workspace)
        s = AgentSession(
            self, sid, task, ws,
            model or self.config.model,
            max_turns or self.config.max_turns,
            allowed_tools or list(self.config.allowed_tools),
            name=name, folder_id=fobj.id, worktree_repo=worktree_repo,
        )
        self.sessions[sid] = s
        self.folders.touch(fobj.id)
        self.broadcast({"type": "folder_updated", **fobj.to_dict()})
        s.start()
        return s

    def get(self, sid: str) -> Optional[AgentSession]:
        if sid in self.sessions:
            return self.sessions[sid]
        # allow addressing by name
        for s in self.sessions.values():
            if s.name == sid:
                return s
        return None

    def list(self) -> list[dict]:
        return [s.meta() for s in sorted(self.sessions.values(),
                                         key=lambda s: s.created_at)]

    async def remove(self, sid: str) -> bool:
        s = self.get(sid)
        if not s:
            return False
        await s.stop()
        if s.worktree_repo:
            _folders.remove_worktree(Path(s.worktree_repo), s.workspace)
        self.sessions.pop(s.id, None)
        self.broadcast({"type": "agent_removed", "session_id": s.id})
        return True

    def load_persisted(self) -> None:
        """Rehydrate sessions from disk on daemon start (cross-restart resume).

        Previously-running sessions can't resume a live stream, so they land as
        'stopped' but keep their history — a `send` will resume them.
        """
        if not cfg.SESSIONS_DIR.exists():
            return
        for d in sorted(cfg.SESSIONS_DIR.iterdir()):
            if not d.is_dir() or (d.name in self.sessions):
                continue
            meta_f, msg_f = d / "meta.json", d / "messages.json"
            if not (meta_f.exists() and msg_f.exists()):
                continue
            try:
                meta = json.loads(meta_f.read_text(encoding="utf-8"))
                msgs = json.loads(msg_f.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            s = AgentSession(
                self, meta["id"], meta.get("task", ""),
                Path(meta.get("workspace", str(Path.cwd()))),
                meta.get("model", self.config.model),
                meta.get("max_turns", self.config.max_turns),
                meta.get("allowed_tools", list(self.config.allowed_tools)),
                name=meta.get("name"),
                folder_id=meta.get("folder_id", _folders.UNFILED_ID),
                worktree_repo=meta.get("worktree_repo"),
            )
            s.messages = msgs
            s._clean_orphaned_toolcalls()
            s.status = "awaiting_input" if meta.get("status") in ("running", "awaiting_input") else meta.get("status", "stopped")
            s.final_message = meta.get("final_message", "")
            s.turns_used = meta.get("turns_used", 0)
            s.tool_calls = meta.get("tool_calls", 0)
            s.tokens = meta.get("tokens", {"prompt": 0, "completion": 0, "total": 0})
            s.created_at = meta.get("created_at", time.time())
            self.sessions[s.id] = s
