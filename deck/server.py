"""DeepSeek Deck HTTP/WebSocket server.

Two faces on one process:
  - REST + WebSocket for the web UI (humans watch & intervene)
  - REST for the `deck` CLI (the frontier model drives via small in/small out)

The full transcripts live here and on disk; the CLI deliberately returns only
compact summaries so a frontier supervisor never ingests token-heavy output.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse

from . import config as cfg
from .session import SessionManager

STATIC = Path(__file__).parent / "static"


def create_app() -> FastAPI:
    config = cfg.DeckConfig.load()
    cfg.init_dirs()
    manager = SessionManager(config)
    manager.load_persisted()

    app = FastAPI(title="DeepSeek Deck")
    app.state.manager = manager
    app.state.config = config

    # --- UI ---------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return (STATIC / "index.html").read_text(encoding="utf-8")

    @app.get("/health")
    async def health() -> dict:
        return {"ok": True, "model": config.model,
                "sessions": len(manager.sessions),
                "max_concurrency": cfg.MAX_CONCURRENCY}

    # --- agents REST ------------------------------------------------------

    @app.post("/api/agents")
    async def spawn(body: dict) -> JSONResponse:
        task = (body.get("task") or "").strip()
        if not task:
            return JSONResponse({"error": "task is required"}, status_code=400)
        s = await manager.create(
            task=task, name=body.get("name"),
            workspace=body.get("workspace"), model=body.get("model"),
            max_turns=body.get("max_turns"), allowed_tools=body.get("allowed_tools"),
            folder=body.get("folder"),
        )
        return JSONResponse(s.meta(brief=False))

    @app.get("/api/agents")
    async def list_agents() -> dict:
        return {"agents": manager.list(),
                "folders": manager.folder_list(),
                "max_concurrency": cfg.MAX_CONCURRENCY,
                "model": config.model}

    # --- folders REST -----------------------------------------------------

    @app.get("/api/folders")
    async def list_folders() -> dict:
        return {"folders": manager.folder_list()}

    @app.post("/api/folders")
    async def create_folder(body: dict) -> JSONResponse:
        name = (body.get("name") or "").strip()
        if not name:
            return JSONResponse({"error": "name is required"}, status_code=400)
        f = manager.folder_create(
            name=name, workspace=body.get("workspace"),
            isolation=body.get("isolation", "shared"),
            source=body.get("source", "manual"))
        return JSONResponse(f)

    @app.patch("/api/folders/{fid}")
    async def update_folder(fid: str, body: dict) -> JSONResponse:
        ok = True
        if "name" in body:
            ok = manager.folder_rename(fid, (body.get("name") or "").strip())
        if "archived" in body:
            ok = manager.folder_archive(fid, bool(body["archived"])) and ok
        return JSONResponse({"ok": ok}, status_code=200 if ok else 400)

    @app.delete("/api/folders/{fid}")
    async def delete_folder(fid: str) -> JSONResponse:
        ok = await manager.folder_delete(fid)
        return JSONResponse({"ok": ok}, status_code=200 if ok else 400)

    @app.post("/api/folders/{fid}/stop")
    async def stop_folder(fid: str) -> JSONResponse:
        await manager.folder_stop(fid)
        return JSONResponse({"ok": True})

    @app.get("/api/folders/{fid}/dag")
    async def folder_dag(fid: str) -> JSONResponse:
        """Parse the DAG board file in this folder's workspace and overlay agent statuses."""
        import re
        f = manager.folder_get(fid)
        if not f or not f.get("workspace"):
            return JSONResponse({"dag": None})
        ws = Path(f["workspace"])
        if not ws.is_dir():
            return JSONResponse({"dag": None})
        # find a DAG board file
        board = None
        for p in sorted(ws.glob("*_DAG.md"), key=lambda x: x.stat().st_mtime, reverse=True):
            board = p
            break
        if not board:
            for p in sorted(ws.glob("SPRINT_*_DAG.md"), key=lambda x: x.stat().st_mtime, reverse=True):
                board = p
                break
        if not board:
            return JSONResponse({"dag": None})
        text = board.read_text(encoding="utf-8")
        # parse node definitions — scan entire file for ### [x] / ### [ ] headers
        node_entries = re.split(r'\n###\s+', text)
        nodes = {}
        for entry in node_entries:
            m = re.match(r'\[([ x])\]\s+(\S+)', entry)
            if not m:
                continue
            checked = m.group(1) == "x"
            nid = m.group(2)
            model = "supervisor"
            mm2 = re.search(r'\*\*Model:\*\*\s*`?(\w+)`?', entry)
            if mm2:
                model = mm2.group(1)
            deps = []
            dm = re.search(r'\*\*Depends:\*\*\s*(.+)', entry)
            if dm:
                deps = [d.strip() for d in dm.group(1).split(",") if d.strip().lower() != "none"]
            nodes[nid] = {"checked": checked, "model": model, "depends": deps}
        # extract or auto-generate mermaid graph
        mm = re.search(r'```mermaid\s*\n(.*?)```', text, re.DOTALL)
        if mm:
            mermaid = mm.group(1).strip()
        elif nodes:
            # auto-generate graph from node dependencies
            lines = ["graph TD"]
            for nid, nd in nodes.items():
                for dep in nd["depends"]:
                    if dep in nodes:
                        lines.append(f"  {dep} --> {nid}")
            if len(lines) == 1:
                lines.append("  " + " --> ".join(nodes.keys()))
            mermaid = "\n".join(lines)
        else:
            return JSONResponse({"dag": None})
        # overlay agent statuses — match by agent name == node id
        agent_statuses = {}
        for a in manager.list():
            if a.get("folder_id") == fid:
                name = (a.get("name") or "").strip()
                if name:
                    agent_statuses[name] = a.get("status", "unknown")
        # build combined status per node
        for nid, nd in nodes.items():
            if nd["checked"]:
                nd["status"] = "done"
            elif nid in agent_statuses:
                st = agent_statuses[nid]
                nd["status"] = st if st in ("running", "starting") else ("done" if st == "awaiting_input" else st)
            elif nd["model"] == "deepseek":
                nd["status"] = "pending"
            else:
                nd["status"] = "pending"  # supervisor nodes
        return JSONResponse({
            "dag": {
                "file": str(board.name),
                "mermaid": mermaid,
                "nodes": nodes,
            }
        })

    @app.get("/api/agents/{sid}")
    async def get_agent(sid: str) -> JSONResponse:
        s = manager.get(sid)
        if not s:
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse(s.meta(brief=False))

    @app.get("/api/agents/{sid}/events")
    async def get_events(sid: str, since: int = 0) -> JSONResponse:
        s = manager.get(sid)
        if not s:
            return JSONResponse({"error": "not found"}, status_code=404)
        evfile = cfg.SESSIONS_DIR / s.id / "events.jsonl"
        events = []
        if evfile.exists():
            for line in evfile.read_text(encoding="utf-8").splitlines():
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("seq", 0) > since:
                    events.append(ev)
        return JSONResponse({"events": events})

    @app.get("/api/agents/{sid}/result")
    async def result(sid: str) -> JSONResponse:
        """Compact result for the frontier supervisor — the token firewall."""
        s = manager.get(sid)
        if not s:
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse({
            "id": s.id, "name": s.name, "status": s.status,
            "final_message": s.final_message, "error": s.error,
            "turns_used": s.turns_used, "tool_calls": s.tool_calls,
            "tokens": s.tokens, "workspace": str(s.workspace),
        })

    @app.get("/api/agents/{sid}/transcript")
    async def transcript(sid: str) -> JSONResponse:
        """Full message history — only when the supervisor explicitly debugs."""
        s = manager.get(sid)
        if not s:
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse({"messages": s.messages})

    @app.post("/api/agents/{sid}/send")
    async def send(sid: str, body: dict) -> JSONResponse:
        s = manager.get(sid)
        if not s:
            return JSONResponse({"error": "not found"}, status_code=404)
        # handle rewind before send
        rw = body.get("rewind_to_msg")
        if rw is not None:
            removed = s.rewind(int(rw))
        text = (body.get("text") or "").strip()
        if not text:
            return JSONResponse({"error": "text is required"}, status_code=400)
        if not s.send(text):
            return JSONResponse({"error": f"agent is {s.status}; cannot send now"},
                                status_code=409)
        return JSONResponse({"ok": True, "status": s.status})

    @app.post("/api/agents/{sid}/stop")
    async def stop(sid: str) -> JSONResponse:
        s = manager.get(sid)
        if not s:
            return JSONResponse({"error": "not found"}, status_code=404)
        await s.stop()
        return JSONResponse({"ok": True, "status": s.status})

    @app.delete("/api/agents/{sid}")
    async def delete(sid: str) -> JSONResponse:
        ok = await manager.remove(sid)
        return JSONResponse({"ok": ok}, status_code=200 if ok else 404)

    # --- websocket firehose ----------------------------------------------

    @app.websocket("/ws")
    async def ws(websocket: WebSocket) -> None:
        await websocket.accept()
        # Hydrate: current roster + folders first.
        await websocket.send_text(json.dumps(
            {"type": "hello", "agents": manager.list(),
             "folders": manager.folder_list(), "model": config.model}))
        q = manager.subscribe()
        try:
            while True:
                ev = await q.get()
                await websocket.send_text(json.dumps(ev, ensure_ascii=False))
        except WebSocketDisconnect:
            pass
        except Exception:  # noqa: BLE001
            pass
        finally:
            manager.unsubscribe(q)

    return app
