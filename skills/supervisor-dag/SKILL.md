---
name: supervisor-dag
description: >-
  Run a parent-supervisor DAG of Task subagents with a live strike board,
  ready-set waves, file ownership, and conflict rules. Use when the user asks to
  parallelize work with subagents, make/run a sprint DAG, fan out Task agents,
  or manage a multi-lane plan with dependencies.
---

# Supervisor DAG harness

Parent chat = **supervisor** (the frontier model — the *only* non-DeepSeek
component of the session). Every token-heavy worker node is a DeepSeek worker on
the **DeepSeek Deck**, dispatched via the `deck` CLI (see the `deck` skill). A
markdown board is the parallelization harness — not design law.

> **Division of labor.** The supervisor spends frontier tokens only on the big
> picture: the board, wave decisions, reading **compact** node results, merges,
> and any reasoning-heavy judgment (done inline — there are no `claude-task`
> worker subagents). All delegable work is a `Model: deepseek` node that runs on
> the Deck. Collect outcomes with `deck result <id>` (compact), never
> `deck log` (full transcript) unless debugging a failed node.

> **One run = one folder.** At the start of a run, create a Deck folder for it
> and spawn every node into it, so all the run's panels live together in the UI,
> mounted at the project directory:
> `deck folder create --name "<sprint>" --workspace <project dir>` → capture the
> id → `deck spawn --folder <id> …` per node (or a `deck wave` `folder` block).
> Each worker is sandboxed to the folder's directory (no network/install cmds);
> a node's `Allowed files` must live under it.

> **Isolation.** Default `shared` — all nodes work in the one project directory;
> the "one writer per path per wave" rule below already prevents collisions, and
> later-wave nodes see earlier nodes' outputs immediately (no merge-forward). Use
> `deck folder create … --isolation worktree` (git repos only) when a wave has
> multiple nodes that build/test concurrently (they'd otherwise race on compiled
> artifacts) or when you want each node's changes as a clean branch to merge at
> the gate.

## When to use

- Multi-lane work with real dependencies and safe parallel slices
- Multi-file offline coding waves
- User says "DAG", "fan out subagents", "run the harness", "wave"

Do **not** use for a single linear task, or when only exploration is needed
(`explore` subagent alone is enough).

## Layout (durable vs ephemeral)

| Artifact | Path | Lifetime |
|----------|------|----------|
| This skill (law of the harness) | `.claude/skills/supervisor-dag/` | permanent |
| Live strike board | `<project>/SPRINT_<NAME>_DAG.md` (or plan-local) | **delete at `R_CLOSE`** |
| Design / frozen choices | `DESIGN.md`, `DECISIONS.md`, `TODO.md` | permanent |
| Per-wave active lock | `<project>/.claude/.dag-active-lock.json` | **written pre-dispatch, deleted at wave end** |

Ephemeral boards are disposable. Do not leave finished sprint DAGs as standing docs.

Board skeleton: [board-template.md](board-template.md).

## Model routing (mandatory)

Every node on the board declares a `Model:` field in its per-node brief.
The supervisor MUST route dispatch according to that value:

| `Model:` | Dispatch mechanism | Notes |
|---|---|---|
| `deepseek` | `~/Documents/deepseek-deck/bin/deck spawn --folder <run folder id> --task "<...>" [--name <node id>] [--tools ...]` via Bash (create the run's folder first, see above). Capture the printed id. Fan out a whole ready-set by issuing several `deck spawn` calls in one turn, or `deck wave --file <spec>`. Collect with `deck result <id>` (compact). See the `deck` skill. | The supervisor MUST NOT do the delegable work itself. Do NOT use native `Task` subagents for worker nodes — DeepSeek workers are the only workers. |
| `supervisor` | Supervisor executes inline | Coordination + reasoning-heavy judgment (board, merges, tagging, review). The frontier model is the only non-DeepSeek component, so nodes needing frontier judgment are done here inline, not delegated. MUST NOT touch files owned by an in-flight `deepseek` node's workspace. |

There is no default. A node without `Model:` is malformed and MUST NOT be dispatched.
Legacy `claude-task` and `deepseek-runner` routing are retired: worker work →
the Deck; frontier work → the supervisor inline.

## How the harness works

1. **Write the board** from the template (mermaid + lane table + conflicts +
   per-node briefs, each declaring `Model:`). Supervisor owns the file.
2. **Ready set** = unchecked nodes whose depends are all `[x]` (or N/A).
3. **Wave** = one dispatch per ready node that does **not** conflict
   (same file, same shared resource, tip commit).
4. **Pre-dispatch deny lock** for every `Model: deepseek` node in this wave
   (see [Enforcement](#enforcement-real-not-skill-wording-only)).
5. **Dispatch** each ready node per its `Model:` routing.
6. **Strike** `[x]` when a node's exit criteria are met; update the board every wave.
7. **Post-dispatch deny release** — revert the deny lock **before** the next wave.
8. **Never start** a node whose dependencies are open.
9. **Done** = every required node checked → run `R_CLOSE` (docs + delete board + verify no stale deny locks).

Shared schema / tip commit / deploy actions stay **serialized**.

## Subagent prompt contract

Each dispatch payload (Task prompt, or DeepSeek `task` + `context` strings) MUST include:

1. Board path + **node ID**
2. **Allowed files** (exclusive ownership for this wave)
3. Depends / exit criteria (copy from board)
4. Kill deadline / expected runtime
5. Links to binding law (`DESIGN.md`, `DECISIONS.md`, plan doc) — not the whole chat
6. Offline-only unless the node is an explicit land; no destructive/irreversible actions
7. Stop and report if a change would alter a frozen `DECISIONS.md` choice

For `Model: deepseek` nodes specifically:

- The supervisor dispatches via the `deck` CLI (`deck spawn` / `deck wave`); items
  1–7 above go verbatim into the `--task` string (use `--task-file` for long
  tasks). Give each node its own `--workspace`. Collect outcomes with
  `deck result <id>` (compact) — never `deck log` unless debugging a failed node.
- Pre-flight Web-fetched material goes into the task string (workers are offline).
- The supervisor MUST NOT `Read` or `Edit` any file in the node's `Allowed files`
  during dispatch or between dispatch and completion. `Glob` / `LS` remain
  permitted for enumeration only. This is enforced by the deny lock in
  [Enforcement](#enforcement-real-not-skill-wording-only); the rule stated here
  is the intent, the `.dag-active-lock.json` entry consulted by the user-level
  PreToolUse hook is the mechanism.

Supervisor merges tip commits and lands (`G_COMMIT`). Wave-0 agents leave
patches or branch commits; they do not race the tip.

## Conflicts (always declare on the board)

| Resource | Default rule |
|----------|----------------|
| Same source path | One writer per path per wave |
| Same shared resource (server, environment, deploy target) | At most one mutator at a time |
| Tip git commit | Supervisor only |
| Any irreversible/production action | Human-gated; never silent |
| `Model: deepseek` node's `Allowed files` | Supervisor `Read` / `Edit` prohibited for the wave; `Glob` / `LS` permitted for enumeration |

## Enforcement (real, not skill-wording-only)

The lock is a small JSON file at `<project>/.claude/.dag-active-lock.json` that a **user-level PreToolUse hook** consults on every `Read` / `Edit` call. If the target file's absolute path is listed in any ancestor's active lock, the hook returns `permissionDecision: "deny"` and the tool call fails before it can run. This mechanism is **cwd-independent** — it fires even when Claude Code was launched from an unrelated directory, unlike `permissions.deny` in `settings.local.json` (which is scoped to the session's project).

### Prerequisites (one-time setup per machine)

1. `~/.claude/hooks/dag-deny-check.sh` — executable shell script that reads PreToolUse JSON on stdin, walks up from the target file's directory looking for `.claude/.dag-active-lock.json`, and emits a deny decision if the path is listed.
2. `~/.claude/settings.json` — user-level `PreToolUse` hook entry matching `Read|Edit`, pointing at the script. Claude Code hot-reloads hook config on file-watch; no restart needed once installed.

Both artifacts live in the deepseek-mcp-suite install. If either is missing, the skill degrades to prose-only enforcement.

### Pre-dispatch — write the active lock

For every wave that contains at least one `Model: deepseek` node:

1. If `<project>/.claude/.dag-active-lock.json` already exists, STOP and report a stale lock. Do NOT overwrite silently — it indicates an interrupted prior wave.
2. Enumerate every `Allowed files` glob owned by any `Model: deepseek` node in the upcoming wave, resolved to absolute paths.
3. Write `<project>/.claude/.dag-active-lock.json`:

```json
{
  "skill": "supervisor-dag",
  "run_id": "<wave marker, e.g. sprint-name-W0>",
  "paths": [
    "/absolute/path/to/file1",
    "/absolute/path/to/file2"
  ]
}
```

The hook denies any `Read` / `Edit` whose `file_path` matches a literal entry in `paths`.

### Post-completion — release the active lock

After every dispatched deepseek node in the wave has completed and been struck on the board:

1. Delete `<project>/.claude/.dag-active-lock.json`.
2. Only then advance to the next wave.

No backup to restore — the lock file is standalone; other settings are untouched.

### Crash recovery

If a wave is interrupted after the lock is written but before it is deleted, subsequent `Read` / `Edit` on the covered paths continues to fail across sessions. Manual recovery:

```bash
cat <project>/.claude/.dag-active-lock.json   # confirm it's stale
rm  <project>/.claude/.dag-active-lock.json
```

On startup, the supervisor MUST check for a leftover `.dag-active-lock.json` and prompt for cleanup before starting any new wave.

## Supervisor loop

```
while not done:
  ready = nodes with deps satisfied and not conflicting
  if ready empty and unchecked remain: unblock gate or report deadlock

  # per-wave enforcement (mandatory before any dispatch)
  if any ready node has Model == deepseek:
    write <project>/.claude/.dag-active-lock.json listing every
    Model: deepseek node's Allowed files (absolute paths)

  # dispatch (parallel where independent)
  for each ready node, route per Model:
    deepseek     -> `deck spawn --task ... --workspace ...` via Bash (capture id);
                    fan out a ready-set with several spawns in one turn or `deck wave`
    supervisor   -> supervisor executes inline (coordination + frontier judgment)

  on each completion:
    verify exit criteria -> strike [x] -> update board

  # per-wave enforcement release (mandatory before next wave)
  rm <project>/.claude/.dag-active-lock.json

R_CLOSE:
  STATUS/TODO/DECISIONS as required
  verify no leftover .dag-active-lock.json
  delete ephemeral board
```

## Anti-patterns

- Treating a finished sprint board as DESIGN/DECISIONS law
- Two agents editing one file "to go faster"
- Starting dependents before the gate checkbox
- Leaving multi-hour soaks inside a one-hour Done gate
- Reviving old ephemeral boards wholesale as if they were current
- Dispatching a `Model: deepseek` node via a native `Task` subagent instead of the `deck` CLI (burns frontier tokens per worker — the whole thing the Deck exists to avoid)
- Pulling `deck log` (full transcript) into supervisor context for a node that succeeded — use `deck result` (compact); full transcripts are for debugging failures only
- Supervisor reading a delegated node's `Allowed files` "just to see" — even
  when the active lock has a bug and permits it, the intent is prohibited
- Leaving the active lock file in place past the wave boundary (blocks
  the next wave with a stale-lock error)
