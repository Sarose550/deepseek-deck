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

**The binary:** `$DEEPSEEK_DECK_HOME/bin/deck` — call it via Bash. It is
cwd-independent and boots its own daemon on first use. Set `DEEPSEEK_DECK_HOME`
to the repo root before using the Deck:

```bash
export DEEPSEEK_DECK_HOME=~/deepseek-deck
# Persist across sessions:
echo 'export DEEPSEEK_DECK_HOME="$HOME/deepseek-deck"' >> ~/.$(basename $SHELL)rc
```

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

**Single delegation.** `id=$($DEEPSEEK_DECK_HOME/bin/deck spawn --task "…" --workspace /path)`.
Immediately arm a watcher (see [Watching workers](#watching-workers-mandatory)
below) so you're notified instead of polling — then do other work.

**Parallel fan-out.** Issue several `deck spawn` calls **in one turn** (independent
Bash calls run concurrently) or write a spec and `deck wave`. Capture the ids,
then arm one folder-level watcher for the whole set — not per-worker loops, and
not manual `deck ps` checks.

**Full-duplex follow-up.** After a worker is `awaiting_input`, `deck send <id>
"next instruction"` resumes it with full history — no re-spawn, no lost context.
If you expect a long reply, re-arm a watcher on the resumed run too.

**Collect + decide.** For each finished worker, `deck result <id>`. Fold the
compact summaries into your orchestration reasoning. Only if a result says it
failed do you `deck log <id>` to see what happened.

## Watching workers (mandatory)

**Never manually poll `deck ps` for a worker you're not blocked on — arm a
watcher and let the notification come to you.** A background worker with no
watcher armed is a dropped thread: you either burn turns re-checking `deck ps`
yourself, or you forget about it until the user asks. Right after every `deck
spawn` / `deck wave` whose result you need but aren't waiting on synchronously:

These scripts are tested end-to-end against real Deck workers on both bash and
zsh — use them verbatim, don't improvise a variant. Three hazards they
specifically guard against, all three hit in practice (the third one **passed
silent** for an entire watch cycle before being caught — see below):

1. **`status` is a reserved read-only word in zsh.** `status=$(...)` fails
   with `read-only variable: status` on any machine whose shell is zsh
   (`echo $SHELL` — common on macOS). Never bind a variable to that name; the
   scripts below use `st`.
2. **A transient/malformed `deck ps` response must never read as "done."** A
   naive poll that does `id in response? status : "gone"` will occasionally
   see one hiccuped/empty JSON payload from the daemon and misreport `gone`
   for a worker that's still mid-run — a false completion. The scripts below
   only ever conclude "done" on an explicit `awaiting_input`/`error`/`stopped`
   status; a parse failure or missing id maps to `POLLFAIL` and is retried on
   the next tick, never treated as terminal.
3. **zsh does not word-split unquoted `$var` the way bash/sh do.**
   `ids="a b c"; for id in $ids; do …; done` iterates **once**, with `id`
   bound to the literal string `"a b c"` — not three times. On bash this is
   the standard, correct idiom; on zsh it silently degrades to a single
   bogus iteration, which then never matches a real agent id and polls
   `POLLFAIL` forever — a watcher that runs indefinitely, produces zero
   output, and never notifies you, which looks exactly like "still working"
   instead of "broken." This is why a multi-id list below is a real array
   (`ids=(a b c)`) iterated with `for id in "${ids[@]}"` — that syntax
   word-splits correctly and identically in both bash and zsh. Never go back
   to a space-separated string + bare `for id in $ids`.

- **One worker, one notification** — `Bash` with `run_in_background: true`:
  ```bash
  DECK="$DEEPSEEK_DECK_HOME/bin/deck"
  id=<worker-id>
  while :; do
    st=$("$DECK" ps --json 2>/dev/null | python3 -c "
  import json,sys
  try:
      d=json.load(sys.stdin)
      a=[x for x in d['agents'] if x['id']=='$id']
      print(a[0]['status'] if a else 'POLLFAIL')
  except Exception:
      print('POLLFAIL')
  " 2>/dev/null)
    case "$st" in
      awaiting_input|error|stopped) echo "worker $id -> $st"; break ;;
    esac
    sleep 20
  done
  ```
  One completion ping; keep working until it fires.

- **A whole folder / wave (several workers)** — `Monitor` with a command that
  emits one line per worker as it finishes, in arrival order, and exits once
  all are accounted for. Use a real array for the id list (see hazard #3
  above — a space-separated string + bare `for id in $ids` silently breaks on
  zsh) and track "seen" ids in a temp file (avoids associative-array syntax
  differences between bash and zsh):
  ```bash
  DECK="$DEEPSEEK_DECK_HOME/bin/deck"
  ids=(<id1> <id2> <id3>)        # real array — fill in from spawn/wave output
  seen_file=$(mktemp)
  remaining=${#ids[@]}
  while [ "$remaining" -gt 0 ]; do
    out=$("$DECK" ps --json 2>/dev/null)
    for id in "${ids[@]}"; do
      if ! grep -q "^$id\$" "$seen_file" 2>/dev/null; then
        st=$(printf '%s' "$out" | python3 -c "
  import json,sys
  try:
      d=json.load(sys.stdin)
      a=[x for x in d['agents'] if x['id']=='$id']
      print(a[0]['status'] if a else 'POLLFAIL')
  except Exception:
      print('POLLFAIL')
  " 2>/dev/null)
        case "$st" in
          awaiting_input|error|stopped)
            echo "worker $id -> $st"
            echo "$id" >> "$seen_file"
            remaining=$((remaining-1))
            ;;
        esac
      fi
    done
    sleep 20
  done
  echo "wave complete"
  rm -f "$seen_file"
  ```

- Either way, **the watcher's job is only to tell you a worker is done** — it
  must not itself pull `deck log` or dump transcripts; when notified, follow up
  with `deck result <id>` per the token-firewall rule above.
- This replaces the old "check `deck ps` occasionally" habit everywhere it
  appears in this skill (and in `supervisor-dag`, `dsar`, `delegate-to-deepseek`
  when they dispatch through the Deck) — arm the watcher, don't hand-poll, and
  use the scripts above rather than reinventing the polling logic each time.

## Discipline (velocity + token economy)

- **Don't poll in a tight loop, and don't hand-poll at all.** Spawn, arm a
  watcher per [Watching workers](#watching-workers-mandatory), then do other
  useful work or wait for the operator until it notifies you. Manual `deck ps`
  checks spend a Bash call and a turn on something a background watcher does
  for free.
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
