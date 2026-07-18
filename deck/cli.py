"""`deck` — the CLI the frontier supervisor uses to navigate the Deck.

Small in, small out: `spawn` prints just an id, `result` prints just a compact
summary. The token-heavy transcripts stay in the daemon and the web UI. The
daemon is booted on demand the first time a command needs it.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import config as cfg


# --- daemon control --------------------------------------------------------

def _read_daemon() -> dict | None:
    if cfg.DAEMON_FILE.exists():
        try:
            return json.loads(cfg.DAEMON_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
    return None


def _alive(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError):
        return False


def ensure_daemon(port: int | None = None) -> str:
    """Return base_url of a live daemon, booting one if needed."""
    info = _read_daemon()
    if info and _alive(info.get("port", 0)):
        return f"http://127.0.0.1:{info['port']}"

    port = port or cfg.DEFAULT_PORT
    cfg.init_dirs()
    logf = open(cfg.LOG_FILE, "a")
    pkg_parent = str(Path(__file__).resolve().parent.parent)
    env = dict(os.environ, DECK_PORT=str(port))
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "deck.server:create_app",
         "--factory", "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=pkg_parent, stdout=logf, stderr=logf,
        start_new_session=True, env=env,
    )
    cfg.DAEMON_FILE.write_text(
        json.dumps({"pid": proc.pid, "port": port, "started_at": time.time()}),
        encoding="utf-8")
    for _ in range(60):  # up to ~15s
        if _alive(port):
            print(f"deck daemon up on http://127.0.0.1:{port} (pid {proc.pid})",
                  file=sys.stderr)
            return f"http://127.0.0.1:{port}"
        time.sleep(0.25)
    raise SystemExit(f"daemon failed to start; see {cfg.LOG_FILE}")


def stop_daemon() -> None:
    info = _read_daemon()
    if not info:
        print("no daemon recorded", file=sys.stderr)
        return
    try:
        os.kill(info["pid"], 15)
        print(f"stopped daemon pid {info['pid']}", file=sys.stderr)
    except (ProcessLookupError, KeyError, OSError):
        print("daemon not running", file=sys.stderr)
    try:
        cfg.DAEMON_FILE.unlink()
    except OSError:
        pass


# --- HTTP helpers ----------------------------------------------------------

def _req(base: str, method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode())
        except (json.JSONDecodeError, OSError):
            return {"error": f"HTTP {e.code}"}
    except (urllib.error.URLError, OSError) as e:
        return {"error": str(e)}


# --- rendering -------------------------------------------------------------

_STATUS_MARK = {
    "starting": "◌", "running": "●", "awaiting_input": "✓",
    "error": "✗", "stopped": "■",
}


def _fmt_agents(agents: list[dict]) -> str:
    if not agents:
        return "(no agents)"
    rows = [f"{'ID':<8}{'NAME':<18}{'STATUS':<20}{'TURNS':<7}{'TOKENS':<9}WORKSPACE"]
    for a in agents:
        mark = _STATUS_MARK.get(a["status"], "?")
        rows.append(
            f"{a['id']:<8}{(a['name'] or '')[:17]:<18}"
            f"{mark + ' ' + a['status']:<20}{a['turns_used']:<7}"
            f"{a['tokens'].get('total', 0):<9}{a['workspace']}")
    return "\n".join(rows)


def _render_transcript(messages: list[dict]) -> str:
    out = []
    for m in messages:
        role = m.get("role")
        if role == "system":
            continue
        if role == "user":
            out.append(f"\n\033[36m▶ user\033[0m\n{m.get('content','')}")
        elif role == "assistant":
            if m.get("content"):
                out.append(f"\n\033[35m🟣 deepseek\033[0m\n{m['content']}")
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function", {})
                out.append(f"  \033[33m⚙ {fn.get('name')}\033[0m {fn.get('arguments','')[:300]}")
        elif role == "tool":
            content = m.get("content", "")
            head = content[:500] + ("…" if len(content) > 500 else "")
            out.append(f"  \033[90m↳ {head}\033[0m")
    return "\n".join(out)


# --- commands --------------------------------------------------------------

def cmd_up(args):
    base = ensure_daemon(args.port)
    print(base)


def cmd_down(args):
    stop_daemon()


def cmd_health(args):
    base = ensure_daemon(args.port)
    print(json.dumps(_req(base, "GET", "/health"), indent=2))


def cmd_open(args):
    info = _read_daemon()
    if info and _alive(info.get("port", 0)):
        url = f"http://127.0.0.1:{info['port']}"
    else:
        url = ensure_daemon(args.port)
    print(url)
    if args.launch:
        subprocess.run(["open", url])


def cmd_spawn(args):
    base = ensure_daemon()
    task = args.task
    if args.task_file:
        task = Path(args.task_file).read_text(encoding="utf-8")
    elif task == "-":
        task = sys.stdin.read()
    body = {"task": task, "name": args.name, "workspace": args.workspace,
            "model": args.model, "max_turns": args.max_turns, "folder": args.folder}
    if args.tools:
        body["allowed_tools"] = [t.strip() for t in args.tools.split(",") if t.strip()]
    r = _req(base, "POST", "/api/agents", body)
    if not r.get("id"):
        raise SystemExit(f"error: {r.get('error') or r}")
    print(r["id"])
    print(f"spawned {r['id']} ({r['name']}) status={r['status']} → {base}",
          file=sys.stderr)


def cmd_ps(args):
    base = ensure_daemon()
    r = _req(base, "GET", "/api/agents")
    if args.json:
        print(json.dumps(r, indent=2))
    else:
        print(_fmt_agents(r.get("agents", [])))
        print(f"\nmodel={r.get('model')} max_concurrency={r.get('max_concurrency')} → {base}",
              file=sys.stderr)


def cmd_result(args):
    base = ensure_daemon()
    r = _req(base, "GET", f"/api/agents/{args.id}/result")
    if "status" not in r:
        raise SystemExit(f"error: {r.get('error') or r}")
    if args.json:
        print(json.dumps(r, indent=2))
        return
    print(f"[{r['status']}] {r['name']} ({r['id']})  "
          f"turns={r['turns_used']} tool_calls={r['tool_calls']} "
          f"tokens={r['tokens'].get('total', 0)}")
    if r.get("error"):
        print(f"error: {r['error']}")
    print("\n" + (r.get("final_message") or "(no final message yet)"))


def cmd_log(args):
    base = ensure_daemon()
    if args.follow:
        seen = 0
        try:
            while True:
                r = _req(base, "GET", f"/api/agents/{args.id}/events?since={seen}")
                for ev in r.get("events", []):
                    seen = max(seen, ev.get("seq", 0))
                    print(_fmt_event(ev))
                meta = _req(base, "GET", f"/api/agents/{args.id}/result")
                if meta.get("status") in ("awaiting_input", "error", "stopped"):
                    break
                time.sleep(1.0)
        except KeyboardInterrupt:
            pass
        return
    r = _req(base, "GET", f"/api/agents/{args.id}/transcript")
    if "messages" not in r:
        raise SystemExit(f"error: {r.get('error') or r}")
    print(_render_transcript(r.get("messages", [])))


def _fmt_event(ev: dict) -> str:
    t = ev.get("type")
    if t == "turn_start":
        return f"— turn {ev.get('turn')} —"
    if t == "tool_call":
        return f"  ⚙ {ev.get('name')} {json.dumps(ev.get('args', {}))[:200]}"
    if t == "tool_result":
        mark = "✓" if ev.get("ok") else "✗"
        return f"  ↳ {mark} {str(ev.get('result',''))[:200]}"
    if t == "user_message":
        return f"▶ user: {ev.get('text','')[:200]}"
    if t == "response_done":
        return f"✓ done ({ev.get('turns_used')} turns, {ev.get('tokens',{}).get('total',0)} tokens)"
    if t == "error":
        return f"✗ error: {ev.get('message')}"
    if t == "status":
        return f"· status={ev.get('status')}"
    return ""


def cmd_send(args):
    base = ensure_daemon()
    text = args.text
    if text == "-":
        text = sys.stdin.read()
    r = _req(base, "POST", f"/api/agents/{args.id}/send", {"text": text})
    if not r.get("ok"):
        raise SystemExit(f"error: {r.get('error') or r}")
    print(f"sent → {args.id} (status={r.get('status')})", file=sys.stderr)


def cmd_stop(args):
    base = ensure_daemon()
    r = _req(base, "POST", f"/api/agents/{args.id}/stop")
    print(f"stopped {args.id} (status={r.get('status')})", file=sys.stderr)


def cmd_rm(args):
    base = ensure_daemon()
    r = _req(base, "DELETE", f"/api/agents/{args.id}")
    print(f"removed {args.id}: ok={r.get('ok')}", file=sys.stderr)


def cmd_wave(args):
    """Spawn several agents from a JSON spec into one folder.

    Spec forms:
      [{task,name?,workspace?,...}, ...]                        # agents only
      {"folder": {"name","workspace"?,"isolation"?}, "agents":[...]}

    With a folder block, the folder is created (mounted at its directory) and
    every agent is spawned into it. Prints one `name<TAB>id` line per agent."""
    base = ensure_daemon()
    spec = json.loads(Path(args.file).read_text(encoding="utf-8"))
    folder_id = None
    if isinstance(spec, dict):
        fblock = spec.get("folder")
        if fblock:
            fr = _req(base, "POST", "/api/folders", {**fblock, "source": "dag"})
            if fr.get("id"):
                folder_id = fr["id"]
                print(f"folder\t{fr['name']}\t{folder_id}", file=sys.stderr)
        spec = spec.get("agents", [])
    for item in spec:
        if folder_id and "folder" not in item:
            item = {**item, "folder": folder_id}
        r = _req(base, "POST", "/api/agents", item)
        if not r.get("id"):
            print(f"{item.get('name','?')}\tERROR: {r.get('error') or r}")
        else:
            print(f"{r['name']}\t{r['id']}")


def cmd_folder(args):
    base = ensure_daemon()
    if args.faction == "ls":
        r = _req(base, "GET", "/api/folders")
        for f in r.get("folders", []):
            tag = " [archived]" if f.get("archived") else ""
            ws = f.get("workspace") or "(scratch)"
            print(f"{f['id']:<8}{f['name'][:24]:<26}{f['isolation']:<10}{ws}{tag}")
        return
    if args.faction == "create":
        body = {"name": args.name, "workspace": args.workspace,
                "isolation": args.isolation, "source": args.source}
        r = _req(base, "POST", "/api/folders", body)
        if not r.get("id"):
            raise SystemExit(f"error: {r.get('error') or r}")
        print(r["id"])
        print(f"folder {r['id']} ({r['name']}) mounted at {r.get('workspace') or '(scratch)'}",
              file=sys.stderr)
        return
    if args.faction == "rename":
        r = _req(base, "PATCH", f"/api/folders/{args.id}", {"name": args.name})
        print(f"renamed {args.id}: ok={r.get('ok')}", file=sys.stderr)
        return
    if args.faction in ("archive", "unarchive"):
        r = _req(base, "PATCH", f"/api/folders/{args.id}",
                 {"archived": args.faction == "archive"})
        print(f"{args.faction} {args.id}: ok={r.get('ok')}", file=sys.stderr)
        return
    if args.faction == "rm":
        r = _req(base, "DELETE", f"/api/folders/{args.id}")
        print(f"deleted folder {args.id} (+its agents): ok={r.get('ok')}", file=sys.stderr)
        return
    if args.faction == "stop":
        r = _req(base, "POST", f"/api/folders/{args.id}/stop")
        print(f"stopped all running agents in folder {args.id}: ok={r.get('ok')}", file=sys.stderr)
        return


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="deck", description="DeepSeek Deck — parallel DeepSeek subagent runtime")
    sub = p.add_subparsers(dest="cmd", required=True)

    up = sub.add_parser("up", help="start the daemon")
    up.add_argument("--port", type=int, default=None)
    up.set_defaults(func=cmd_up)

    dn = sub.add_parser("down", help="stop the daemon")
    dn.set_defaults(func=cmd_down)

    he = sub.add_parser("health", help="daemon health")
    he.add_argument("--port", type=int, default=None)
    he.set_defaults(func=cmd_health)

    op = sub.add_parser("open", help="print (and optionally open) the UI URL")
    op.add_argument("--port", type=int, default=None)
    op.add_argument("--launch", action="store_true", help="open in browser")
    op.set_defaults(func=cmd_open)

    sp = sub.add_parser("spawn", help="spawn a DeepSeek worker; prints its id")
    sp.add_argument("--task", default="", help="task text, or '-' for stdin")
    sp.add_argument("--task-file", default=None)
    sp.add_argument("--name", default=None)
    sp.add_argument("--workspace", default=None)
    sp.add_argument("--model", default=None)
    sp.add_argument("--max-turns", dest="max_turns", type=int, default=None)
    sp.add_argument("--tools", default=None, help="comma list e.g. Read,Bash,Edit")
    sp.add_argument("--folder", default=None, help="folder id or name (created if new); default Unfiled")
    sp.set_defaults(func=cmd_spawn)

    ps = sub.add_parser("ps", help="list agents")
    ps.add_argument("--json", action="store_true")
    ps.set_defaults(func=cmd_ps)

    rs = sub.add_parser("result", help="compact result for a worker (token firewall)")
    rs.add_argument("id")
    rs.add_argument("--json", action="store_true")
    rs.set_defaults(func=cmd_result)

    lg = sub.add_parser("log", help="render a worker's transcript")
    lg.add_argument("id")
    lg.add_argument("--follow", action="store_true", help="stream events until done")
    lg.set_defaults(func=cmd_log)

    sd = sub.add_parser("send", help="send a follow-up message (resumes the worker)")
    sd.add_argument("id")
    sd.add_argument("text", help="message text, or '-' for stdin")
    sd.set_defaults(func=cmd_send)

    st = sub.add_parser("stop", help="stop a running worker")
    st.add_argument("id")
    st.set_defaults(func=cmd_stop)

    rm = sub.add_parser("rm", help="remove a worker")
    rm.add_argument("id")
    rm.set_defaults(func=cmd_rm)

    wv = sub.add_parser("wave", help="spawn many workers from a JSON spec file (one folder)")
    wv.add_argument("--file", required=True)
    wv.set_defaults(func=cmd_wave)

    fp = sub.add_parser("folder", help="manage folders (create/ls/rename/archive/rm)")
    fsub = fp.add_subparsers(dest="faction", required=True)
    fc = fsub.add_parser("create", help="create a folder; prints its id")
    fc.add_argument("--name", required=True)
    fc.add_argument("--workspace", default=None, help="project directory the folder is mounted at")
    fc.add_argument("--isolation", default="shared", choices=["shared", "worktree"])
    fc.add_argument("--source", default="manual")
    fsub.add_parser("ls", help="list folders")
    frn = fsub.add_parser("rename"); frn.add_argument("id"); frn.add_argument("name")
    far = fsub.add_parser("archive"); far.add_argument("id")
    fun = fsub.add_parser("unarchive"); fun.add_argument("id")
    frm = fsub.add_parser("rm", help="delete a folder and its agents"); frm.add_argument("id")
    fst = fsub.add_parser("stop", help="stop all running agents in a folder (keeps conversations)"); fst.add_argument("id")
    fp.set_defaults(func=cmd_folder)

    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
