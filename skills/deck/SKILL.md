---
name: deck
description: >-
  Drive the DeepSeek Deck — an external, parallel, resumable DeepSeek subagent
  runtime with a Claude-Code-lookalike web UI. Use this whenever you would spawn
  token-heavy worker subagents: instead of native Task subagents (which burn
  frontier tokens), delegate the heavy lifting to DeepSeek workers you spawn,
  watch, message, and collect via the `deck` CLI. The frontier model stays the
  orchestrator; DeepSeek does the bulk work; the operator watches live panels.
  Triggers: "deck", "spawn a worker", "fan out deepseek", "delegate to deepseek",
  running many subagents in parallel outside Claude Code's native harness.
---

# DeepSeek Deck — external subagent runtime

The Deck is an external application that gives DeepSeek workers the same
first-class treatment Claude Code gives native Task subagents — a separate live
panel per worker, resumable full-duplex messaging, run-many-in-parallel — but
**without spending frontier tokens per worker**. Anthropic doesn't let us extend
Claude Code natively; this skill is the seam. Just as you implicitly know how to
drive native subagents, this skill teaches you to drive the Deck.

**The binary:** `~/Documents/deepseek-deck/bin/deck` (call it via Bash; it is
cwd-independent and boots its own daemon on first use).

## Division of labor (the whole point)

- **You (frontier model)** = orchestrator. Spend tokens only on the big picture:
  composing tasks, deciding what to parallelize, reading **compact** results,
  deciding next steps.
- **DeepSeek workers** = the token-heavy execution (reading files, editing,
  running builds, batch work). Their verbose transcripts live in the Deck and
  its UI — **never in your context** unless you explicitly debug one.
- **Operator (human)** = opens the web UI to watch panels stream live and can
  intervene (send messages, stop) alongside you.

**Token-firewall rule (mandatory):** to learn a worker's outcome use
`deck result <id>` (compact: final summary + status + tokens). Do **not** use
`deck log <id>` (full transcript) unless a worker failed and you are debugging
it. Pulling full transcripts defeats the entire purpose.

## Verbs

| Command | Purpose |
|---|---|
| `deck up` | Start the daemon (auto-started by any command; call to get the URL). |
| `deck open` | Print the UI URL (`--launch` opens a browser). Tell the operator to open it. |
| `deck spawn --task "…" [--folder ID] [--name X] [--workspace DIR] [--model M] [--max-turns N] [--tools "Read,Bash,…"]` | Spawn one worker. **Prints only the id** on stdout. `--folder` puts it in a folder (created if the name is new); default is **Unfiled**. `--task -` reads stdin; `--task-file F` reads a file (use for long tasks). |
| `deck wave --file spec.json` | Spawn many workers into **one folder**. Spec: `{"folder":{"name","workspace","isolation"},"agents":[{task,name,…}]}`. Prints `name<TAB>id` per line. |
| `deck folder create --name X [--workspace DIR] [--isolation shared\|worktree]` | Make a folder; **prints its id**. `--workspace` is the project directory its agents are mounted at. |
| `deck folder ls` / `rename <id> <name>` / `archive <id>` / `unarchive <id>` / `rm <id>` | Manage folders. `rm` deletes the folder **and its agents**. |
| `deck ps [--json]` | List all workers with status/turns/tokens. Your cheap poll. |
| `deck result <id> [--json]` | **Compact** outcome — use this to collect a worker's result. |
| `deck send <id> "…"` | Send a follow-up into a worker (resumes it). Full-duplex, like SendMessage. `"-"` reads stdin. |
| `deck log <id> [--follow]` | Full transcript. Debug-only — avoid by default (token firewall). |
| `deck stop <id>` / `deck rm <id>` | Stop / remove a worker. |
| `deck down` | Stop the daemon. |

Worker `status`: `starting` → `running` → `awaiting_input` (done a response,
resumable) → `error` / `stopped`.

## Folders (grouping + where agents are mounted)

A **folder** groups several agent panels in the UI and carries the **project
directory** its agents run in. Every agent belongs to exactly one folder, fixed
at creation.

- **A DAG run = one folder.** Before dispatching a board, `deck folder create
  --name "<sprint>" --workspace <project dir>` and spawn every node with
  `--folder <that id>`. All the run's panels then live together in the UI, mounted
  at the project. (Or use `deck wave` with a `folder` block.)
- **Ad-hoc workers** with no `--folder` land in **Unfiled** and run in isolated
  scratch dirs — fine for throwaway tasks, not project work.
- **Isolation** (folder-level): `shared` (default) — all agents work in the same
  project directory; the harness's per-wave file-ownership already prevents
  collisions, and later nodes see earlier nodes' files immediately. `worktree` —
  each agent gets its own `git worktree` on its own branch (git repos only); use
  it when nodes build/test concurrently and would otherwise race on artifacts, or
  when you want each agent's changes as an isolated branch to merge.

## Workflow patterns

**Single delegation.** `id=$(~/Documents/deepseek-deck/bin/deck spawn --task "…" --workspace /path)`.
Do other work. Later: `deck ps` to check; `deck result $id` when `awaiting_input`.

**Parallel fan-out.** Issue several `deck spawn` calls **in one turn** (independent
Bash calls run concurrently) or write a spec and `deck wave`. Capture the ids.
The workers run truly in parallel (daemon caps concurrency, default 12). Poll
with a single `deck ps`, not per-worker loops.

**Full-duplex follow-up.** After a worker is `awaiting_input`, `deck send <id>
"next instruction"` resumes it with full history — no re-spawn, no lost context.

**Collect + decide.** For each finished worker, `deck result <id>`. Fold the
compact summaries into your orchestration reasoning. Only if a result says it
failed do you `deck log <id>` to see what happened.

## Discipline (velocity + token economy)

- **Don't poll in a tight loop.** Spawn, then either do other useful work or
  wait for the operator; check `deck ps` occasionally. Each poll is a Bash call,
  not free frontier reasoning.
- **Give workers a confined workspace** (`--workspace`) so parallel workers don't
  clobber each other. Workers are sandboxed to their workspace and cannot run
  network/install commands (curl/wget/ssh/pip install are blocked) — pre-fetch
  anything external yourself and pass it in the task.
- **Write self-contained tasks.** DeepSeek workers don't share your context.
  Put file paths, conventions, and exact success criteria in the `--task`.
- **Tell the operator the URL** (`deck open`) so they can watch and intervene.
- **Never let a worker mutate git state outside its own edits.** A worker told
  "this file is read-only, propose a patch instead" is not thereby blocked from
  running `git apply` (bare), `git checkout --`, `git stash`, `git reset`, or
  `git restore` — those commands are not scoped to the worker's `Allowed files`
  and can silently revert or discard *unrelated* uncommitted work elsewhere in
  the tree. This happened in practice: a worker self-testing a patch ran
  `git apply` then `git checkout -- <two files>` to "clean up", reverting ~260
  lines of unrelated uncommitted work that a human had not yet committed.
  Any task whose brief includes "don't edit X, write a patch instead" MUST
  explicitly forbid these commands and permit only `git apply --check` /
  `git diff` / `diff -u` for self-verification. State this in the `--task`
  itself — prose telling the worker "don't touch X" is not enough; a worker
  will readily apply-then-revert if `Allowed files` doesn't also spell out
  which git subcommands are off-limits.

## Relationship to native subagents

Use the Deck for **token-heavy delegable work** (edits, builds, batch ops,
scans). Reserve your own inline reasoning for orchestration and judgment. Under
the `supervisor-dag` skill, every `Model: deepseek` node dispatches here; the
frontier model is the only non-DeepSeek component of the session.
