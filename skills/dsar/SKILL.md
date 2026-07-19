---
name: dsar
description: Adversarial AI code/plan review where the CRITIC and RESPONSE roles run as DeepSeek workers on the DeepSeek Deck (live panels; `deck spawn`). Claude orchestrates and issues the final ship/no-ship verdict; it never Reads or Edits target source during the loop — enforced by a per-run active-lock file consulted by a user-level PreToolUse hook. Auto-detects plan/code/code-vs-plan mode.
user_invocable: true
---

# Adversarial Code Review (DeepSeek-only)

> **Actors.** Every code-touching action goes through DeepSeek on the **DeepSeek Deck** (`$DEEPSEEK_DECK_HOME/bin/deck`, see the `deck` skill) — CRITIC and RESPONSE run as Deck workers with live panels the operator can watch. Claude orchestrates: composes prompts, writes and revokes the deny lock, reads the review/response artifacts, and issues the final ship/no-ship verdict on top of DeepSeek's per-round `VERDICT: APPROVED|REVISE`. **Claude MUST NOT `Read` or `Edit` any file in `TARGET_PATHS` between the deny lock's write (Step 4.2) and its release (Step 8).**

> **Deck dispatch overrides (read first — they supersede the legacy mechanics below).**
> 1. **Scratch location.** Deck workers are sandboxed to their `--workspace`, so they cannot write `/tmp`. Wherever this skill says `/tmp/dsar-<...>-${REVIEW_ID}.md`, use `${REPO_ROOT}/.dsar/<...>-${REVIEW_ID}.md` instead. Create `${REPO_ROOT}/.dsar/` at Step 2 and add it to `.gitignore` if not already ignored. These artifacts are NOT in `TARGET_PATHS`, so Claude may freely `Read` them (only the source under review is deny-locked).
> 2. **Dispatch.** Every CRITIC / RESPONSE dispatch runs as a Deck worker:
>    ```bash
>    id=$($DEEPSEEK_DECK_HOME/bin/deck spawn \
>          --name dsar-critic-${REVIEW_ID} --workspace "${REPO_ROOT}" \
>          --task-file /tmp/dsar-critic-body-${REVIEW_ID}.md \
>          --tools "Read,Glob,Grep,Bash,Write")           # RESPONSE adds Edit
>    ```
>    (The *prompt body* files under `/tmp/dsar-*-body-*.md` are written by Claude, not a worker, so `/tmp` is fine for those.) Then arm a watcher for this worker (Monitor, or backgrounded Bash — see the `deck` skill's "Watching workers" section) instead of hand-polling `deck ps`; when it reports `awaiting_input`, `Read` the artifact it wrote under `${REPO_ROOT}/.dsar/`. CRITIC tools omit `Edit` (auditor); RESPONSE gets the full set including `Edit`. One worker per role per round (fresh spawn each round — matches the stateless design).
> 3. **No native Task subagents, no direct MCP.** The `deepseek-runner` wrapper and direct `mcp__deepseek__delegate_to_deepseek` paths are retired here — worker cost stays on DeepSeek, orchestration stays with Claude.

Three-role loop on existing changes/plan:

- **WRITER (round-0)** — the user's existing changes or plan. The skill does not re-write from scratch; the loop opens at the CRITIC.
- **CRITIC** — a separately-scoped DeepSeek call; receives the target artifact only; instructed to assume it is flawed until proven otherwise.
- **RESPONSE** — a third DeepSeek call; receives the critic's findings + the current artifact; produces both a structured reply (accept / reject-with-reasoning / re-scope) AND the revised artifact in a single call.

Round 1 = CRITIC → verdict. Rounds 2–5 = RESPONSE → CRITIC → verdict. Max 5 rounds.

Enforcement is real, not skill-wording-only: `<REPO_ROOT>/.claude/.dsar-active-lock.json` is written before the first dispatch, listing every target-file absolute path. A user-level PreToolUse hook (`~/.claude/hooks/dag-deny-check.sh`, registered in `~/.claude/settings.json`) consults it on every `Read` / `Edit` and returns `permissionDecision: "deny"` if the target path is covered. Lock deleted at terminal state. Mechanism is cwd-independent.

> **DeepSeek sandbox.** The DeepSeek MCP's workspace root is set in `~/.deepseek-mcp/config.json` (`workspace` field, default `~`). `REPO_ROOT` must resolve inside it, otherwise the RESPONSE call will refuse writes to target files.

## When to invoke

- `/dsar` — auto-detect
- `/dsar plan` — force plan review
- `/dsar code` — force code review
- `/dsar <file-path>` — review a specific file (argument contains `/` or `.`)

Codex-era overrides (`model:`, `sandbox:`, `approvals:`, reasoning-level flags) are REMOVED. DeepSeek model / thinking / workspace settings live in `~/.deepseek-mcp/config.json` and are governed there — not per-invocation on this skill.

## Instructions

> **Placeholders.** `${REVIEW_ID}`, `${REPO_ROOT}`, `${BASE_BRANCH}` are template placeholders substituted with literal values at capture time, not shell variables.

### Step 1 — Determine review mode + detect operator language

Priority: explicit argument → Plan Mode → auto-detect via the matrix (unstaged / staged / branch diff × plan-in-context). If no code changes and no plan in context, ask the user.

| Code changes? | Plan in context? | Mode |
|--------------|-----------------|------|
| No | Yes | **plan** |
| Yes | Yes | **code-vs-plan** |
| Yes | No | **code** |
| No | No | Ask the user |

**Detect operator language.** Inspect the last few human-authored messages. If predominantly non-English, capture `OPERATOR_LANGUAGE = <name>` (else `English`). Runtime prose to the operator uses this; machine-readable literals (`[severity:]`, `VERDICT:`, section headers, evaluation-matrix column names) stay English.

### Step 2 — REVIEW_ID + REPO_ROOT + BASE_BRANCH

- `REVIEW_ID`: `{unix_timestamp}-{random_8digit_number}`. Substitute the literal into all subsequent commands.
- `REPO_ROOT`: `git rev-parse --show-toplevel`. Abort on exit 128 (not a work tree) or shell-special characters in the path.
- Submodule warning: `git rev-parse --show-superproject-working-tree` — if non-empty, warn that review is scoped to the submodule.
- `BASE_BRANCH` (code / code-vs-plan only): `git symbolic-ref refs/remotes/origin/HEAD | sed 's|refs/remotes/origin/||'`, fallback `main` else `master`.

### Step 3 — Prepare review material + capture TARGET_PATHS

- **Plan mode:** file path if it exists, else `Write` to `/tmp/dsar-plan-${REVIEW_ID}.md`. Print the plan path so the operator can open it.
- **Code mode:** merge `git diff --name-only` (unstaged + staged) into unique paths; fallback to `git diff --name-only ${BASE_BRANCH}...HEAD` if both empty; abort if all three empty.
- **Code-vs-plan mode:** both.

**Save `TARGET_PATHS`** in memory as the enumerated list of paths the deny lock will cover — one entry per file, always. Even when the list is long (>50 files) the enumeration is preserved rather than collapsed to a broad glob like `**/*`. A precise deny list has the same enforcement value as a broad one and avoids blocking legitimate work on unrelated files elsewhere in the repo during a long review.

- `plan` → `[<plan-file-path>]`
- `code` / `code-vs-plan` → the enumerated file list from `git diff --name-only`

If the operator's changeset is so large that the lock file becomes unwieldy in practice, that is a signal to break the review into smaller batches, not to trade precision for convenience by broadening the lock.

### Step 4 — Write deny lock, compose CRITIC prompt, dispatch DeepSeek

**Step 4.1 — Compose the CRITIC prompt body.**

Use the mode-appropriate template (plan / code / code-vs-plan) with all substitutions (`${BASE_BRANCH}`, `<plan-path>`, `<file list>`, `<git diff commands>`, `<language>` block when non-English). Replace the reviewer-permissions block with:

```
<reviewer_permissions>
You are DeepSeek acting as an offline adversarial critic.

You MAY:
  - Read source files, tests, docs under the working directory.
  - Run read-only shell commands (git log, grep, ls, wc, cat, head, tail,
    git diff, git status, git apply --check).

You MAY NOT:
  - Edit, create, or delete any file inside the repository (the review file
    named in your output contract is the sole exception).
  - Commit, push, or run any build/test command that mutates state.
  - Run `git apply` WITHOUT `--check`, `git checkout`, `git stash`,
    `git reset`, `git restore`, or any other command that writes to the
    working tree or index — even "to see what it looks like" or "to test
    then revert." These are shell commands, not file-edit-tool calls, and
    are therefore NOT covered by "don't edit files" unless named explicitly:
    a prior critic run applied a patch for real and then ran
    `git checkout --` to "clean up," silently reverting unrelated
    uncommitted source that had nothing to do with the review.
  - Make network calls; state external assumptions explicitly instead.

You are an auditor, not a contributor. The response role applies fixes;
you find issues. If you need to know what a file would look like with a
patch applied, read the patch and the file and reason about it — do not
apply it for real.
</reviewer_permissions>
```

Prompt-body templates for plan / code / code-vs-plan (`<role>`, `<operating_stance>`, `<task>`, `<attack_surface>`, `<finding_bar>`, `<scope_exclusions>`, `<calibration>`, `<output_format>`) match the upstream adversarial-review shape and are omitted here for brevity — see the upstream skill's Step 4 templates. Substitute all `${...}` placeholders before writing to disk.

Write the substituted prompt to `/tmp/dsar-critic-body-${REVIEW_ID}.md`.

**Step 4.2 — Write the pre-dispatch active lock.**

Prerequisites (one-time per machine): `~/.claude/hooks/dag-deny-check.sh` executable + `PreToolUse` matcher `Read|Edit` entry in `~/.claude/settings.json` pointing at it. If either is missing, degrade to prose-only enforcement and surface a warning.

For `TARGET_PATHS`:

1. **Stale-lock check.** If `<REPO_ROOT>/.claude/.dsar-active-lock.json` already exists, STOP and report a stale lock. Do NOT overwrite — it indicates an interrupted prior run. See [Crash recovery](#crash-recovery).
2. **Write** `<REPO_ROOT>/.claude/.dsar-active-lock.json`:

   ```json
   {
     "skill": "dsar",
     "run_id": "<REVIEW_ID>",
     "paths": [
       "/absolute/path/1",
       "/absolute/path/2"
     ]
   }
   ```

   One absolute-path entry per file in `TARGET_PATHS`, always enumerated. Filename distinct from supervisor-dag's `.dag-active-lock.json` so both skills can coexist in one project; the hook checks both.
3. Emit: `🔒 Active lock written for <N> target path(s). Claude is blocked from Read/Edit on the review target for the duration of this run.` Translate to `OPERATOR_LANGUAGE`.

**Step 4.3 — Dispatch the CRITIC** (per the Deck dispatch override at the top).

Append the CONTEXT/output-contract to the bottom of `/tmp/dsar-critic-body-${REVIEW_ID}.md` before spawning:

```
CONTEXT:
  Role: CRITIC in an adversarial-review loop. Round: <N>.
  REPO_ROOT: ${REPO_ROOT}
  Target paths: <TARGET_PATHS newline-separated>.
  Output contract:
    1. Write the review to ${REPO_ROOT}/.dsar/review-${REVIEW_ID}.md
       following the <output_format> section of the task body.
    2. Do NOT edit, create, or delete any source file under REPO_ROOT
       (only the .dsar/ review file).
    3. This run is offline; state any external assumption explicitly.
```

Then spawn a Deck worker (auditor tools, no `Edit`):

```bash
id=$($DEEPSEEK_DECK_HOME/bin/deck spawn \
      --name dsar-critic-${REVIEW_ID} --workspace "${REPO_ROOT}" \
      --task-file /tmp/dsar-critic-body-${REVIEW_ID}.md \
      --tools "Read,Glob,Grep,Bash,Write")
```

Arm a watcher for `id` (Monitor, or backgrounded Bash — see the `deck` skill's "Watching workers" section) instead of hand-polling `deck ps`; when it reports `awaiting_input`, proceed to Step 4.4. Do not read `TARGET_PATHS`; only `.dsar/review-${REVIEW_ID}.md`.

> **Scope note.** The active lock binds *Claude*, not the DeepSeek sub-session. The sub-session runs outside Claude Code's permission harness through MCP and MUST be allowed to read the target — otherwise the review is meaningless. What the sub-session cannot mutate is guarded by the `<reviewer_permissions>` prompt block, which is wording, not a hard block. Documenting rather than pretending otherwise.

**Step 4.4 — Sanity-check the review file.**

Read `/tmp/dsar-review-${REVIEW_ID}.md` (permitted — `/tmp/` is not in `TARGET_PATHS`).

- File missing or empty → offer ONE retry of 4.3 (does not consume the round counter).
- No `^VERDICT: (APPROVED|REVISE)$` line → offer ONE retry.
- `VERDICT: REVISE` and zero `\[severity:` matches → offer ONE retry.

After a failed retry, jump to Step 8's "Aborted — no valid review" branch (which releases the deny lock).

### Step 5 — Show the review verbatim, route on verdict

**Show the review verbatim. Mandatory and blocking.**

```
## Adversarial Review — Round N (mode: <plan|code|code-vs-plan>)

<verbatim contents of /tmp/dsar-review-${REVIEW_ID}.md>
```

Rules: no preamble, no summary, no code-fence wrapping, no fix-applying tool call in the same message.

**After the review message is sent**, route on VERDICT:

- `APPROVED` → Step 8 (Done).
- `REVISE` → Step 6 (RESPONSE dispatch).
- Round ≥ 5 → Step 8 (max rounds).

### Step 6 — Dispatch RESPONSE (revise artifact + structured reply, one call)

**Precondition gate.** Confirm Step 5's verbatim review message has been sent this round. If not, STOP and return to Step 5.

**Step 6.1 — Compose the RESPONSE prompt body.**

Write to `/tmp/dsar-response-body-${REVIEW_ID}.md`:

```
<role>
You are DeepSeek in the RESPONSE role of an adversarial-review loop.
Round: <N>.
</role>

<inputs>
1. Critic review at /tmp/dsar-review-${REVIEW_ID}.md — Read it now.
2. Target artifact at ${REPO_ROOT}, files under review:
   <TARGET_PATHS>
3. Plan file (if code-vs-plan or plan mode): <path or "N/A">
</inputs>

<task>
For every finding in the review, choose exactly one action:
  - `accept` — valid; apply the fix (or a minimal variant that resolves it).
  - `reject with reasoning` — technically wrong, out of scope, or contradicts
    a user requirement; prepare a concrete counter-argument. Do NOT apply.
  - `re-scope` — partially valid; apply a narrower fix and explain the narrowing.

For every `accept` and `re-scope` decision, edit the target file(s) directly.
You MAY Edit/Write files under ${REPO_ROOT}. You MUST NOT modify files outside
the target paths listed in <inputs>.
</task>

<verification>
- If a quick offline test is possible, run it. No network, no destructive
  commands, no build/test that mutates paths outside the target list.
- Self-verification uses `git apply --check` / `git diff` / `diff -u` only.
  NEVER run `git apply` (bare), `git checkout`, `git stash`, or `git reset` —
  these are not scoped to the target list and can silently revert or discard
  unrelated uncommitted work elsewhere in the tree. (This has happened: a
  worker self-testing a patch ran `git apply` then `git checkout --` to
  "clean up" and reverted ~260 lines of unrelated uncommitted source.)
- If a fix breaks a detectable syntax check or removes needed content,
  UNDO it and downgrade the row to `reject with reasoning`.
- If verification would require network access, state the claim as a
  hypothesis rather than as fact.
</verification>

<scope_guard>
- Never edit files outside the target list.
- Never commit, push, or run destructive commands.
- Never run `git apply` (bare), `git checkout`, `git stash`, or `git reset` —
  self-verify with `git apply --check` / `git diff` / `diff -u` only.
- Never invent findings the critic didn't raise; do not rephrase existing
  findings (they carry through verbatim to the next CRITIC round).
</scope_guard>

<output_format>
Write to /tmp/dsar-response-${REVIEW_ID}.md:

# Response — Round N

## Evaluation matrix
| # | Severity | Action                 | Type                                             |
|---|----------|------------------------|--------------------------------------------------|
| 1 | high     | accept                 | architectural | tool-mechanic | style | security |
| … | …        | …                      | …                                                |

## Applied
- [#N]: <one-line summary of the change, with cited file/lines>
- …

## Re-scoped
- [#N]: <what was applied, why narrower>

## Rejected with reasoning
- [#N]: <concrete counter-argument>

## Specific asks for the next CRITIC round
1. Are the rejections technically valid?
2. Do the applied fixes resolve the original findings?
3. Did the fixes introduce any new issues?
</output_format>
```

Append the `<language>` block when `OPERATOR_LANGUAGE != English`. In `<output_format>`, section headers and severity/action tokens stay in English regardless.

**Step 6.2 — Verify the deny lock is intact.**

Read `<REPO_ROOT>/.claude/.dsar-active-lock.json`. Confirm the `paths` array still contains every entry for `TARGET_PATHS`. If mutated (operator intervention, concurrent skill), STOP and report — do NOT re-write silently.

**Step 6.3 — Dispatch RESPONSE** (per the Deck dispatch override at the top).

Append to the bottom of `/tmp/dsar-response-body-${REVIEW_ID}.md` before spawning:

```
CONTEXT:
  Role: RESPONSE. Round: <N>.
  REPO_ROOT: ${REPO_ROOT}
  Target paths (writable): <TARGET_PATHS>.
  You MAY Edit/Write the target source files listed above.
  You MUST NOT touch source outside the target paths.
  Output: write the structured reply to
          ${REPO_ROOT}/.dsar/response-${REVIEW_ID}.md.
```

Then spawn a Deck worker with the full editing toolset:

```bash
id=$($DEEPSEEK_DECK_HOME/bin/deck spawn \
      --name dsar-response-${REVIEW_ID} --workspace "${REPO_ROOT}" \
      --task-file /tmp/dsar-response-body-${REVIEW_ID}.md \
      --tools "Read,Write,Edit,Bash,Glob,Grep")
```

Arm a watcher (Monitor, or backgrounded Bash) instead of hand-polling `deck ps`;
when it reports `awaiting_input`, proceed to Step 6.4. The worker edits
the target source in place inside its workspace; Claude verifies via the next
CRITIC round, never by reading `TARGET_PATHS` directly.

**Step 6.4 — Sanity-check the response file.**

Read `/tmp/dsar-response-${REVIEW_ID}.md` (permitted — under `/tmp/`).

- Missing or empty → jump to Step 8's "Aborted — RESPONSE failed" branch.
- Missing `## Evaluation matrix` or `## Specific asks` → surface a warning but continue.

**Do NOT Read any file in `TARGET_PATHS` to verify Claude-side that the edits are correct.** The deny lock will fire. Verification is deferred to the next CRITIC round — that's the whole loop.

**Step 6.5 — Show the RESPONSE summary.**

```
### Round N — RESPONSE

**Applied:** <count>   **Re-scoped:** <count>   **Rejected with reasoning:** <count>

(Full evaluation matrix, applied list, and reply structure at
/tmp/dsar-response-${REVIEW_ID}.md — next CRITIC round starts.)
```

Translate to `OPERATOR_LANGUAGE`.

**Step 6.6 — Severity-decline soft signal.**

Track per-round severity across the transcript. If `high` (or worse) persists three consecutive rounds:

```
⚠ Severity has stayed at <level> for <N> rounds. This usually means either
(a) the artifact has a structural problem the fixes don't reach, or (b) the
critic is misreading something the response keeps disagreeing with.
Continue, switch approach, or abort?
```

Soft signal — default to continuing if no operator response.

### Step 7 — Round 2..5 loop

MCP calls are stateless — no session to resume. Each new round is a fresh MCP call with round history injected via prompt context.

Increment the round counter. If rounds ≤ 5:

**Step 7.1 — Compose the round-N critic prompt.**

Same template as Step 4.1, but PREPEND before `<task>`:

```
<prior_response>
The previous round's RESPONSE reply is below. Address it directly in your
next review — verify the applied fixes actually resolve the findings, and
push back on rejections you don't accept:

<verbatim contents of /tmp/dsar-response-${REVIEW_ID}.md>
</prior_response>
```

Overwrite `/tmp/dsar-critic-body-${REVIEW_ID}.md`.

**Step 7.2 — Verify the deny lock is intact** (same as Step 6.2).

**Step 7.3 — Re-dispatch CRITIC** (same as Step 4.3, incremented round). Return to Step 4.4 for sanity, then Step 5 for verbatim display + verdict routing.

### Step 8 — Terminal state: message + Claude verdict + release deny lock

Order at every terminal state:

1. **Per-state header block** (templates below).
2. **Operator summary** in `OPERATOR_LANGUAGE`.
3. **Claude ship/no-ship verdict** — Claude reads `/tmp/dsar-review-${REVIEW_ID}.md` (final CRITIC output) + `/tmp/dsar-response-${REVIEW_ID}.md` (final RESPONSE reply) and issues one of:
   - **SHIP** — DeepSeek approved AND no rejected-with-reasoning findings Claude judges the critic was likely correct about.
   - **SHIP-WITH-CAVEATS** — DeepSeek approved but there are rejections Claude thinks deserve manual follow-up; list them briefly.
   - **NO-SHIP** — max rounds, not verified, aborted, or approved-but-critical-rejection-unresolved.

   Format: `**Claude verdict: SHIP | SHIP-WITH-CAVEATS | NO-SHIP** — <one-sentence reason>`.
4. **Release the deny lock** (see [Post-completion](#post-completion--release-the-deny-lock)).

Terminal state templates:

**Approved:**
```
## Adversarial Review — Summary (mode: <mode>)

**Status:** Approved after N round(s)

[Final review]

---
**Reviewed and approved by the critic. Awaiting your decision.**
```

**Maximum rounds reached:**
```
## Adversarial Review — Summary (mode: <mode>)

**Status:** Maximum reached (5 rounds) — not fully approved

**Remaining findings:**
[Unresolved issues from the last round]

---
**The critic still has findings. Please review them and decide how to proceed.**
```

**Not verified** (RESPONSE dispatch failed after a valid prior CRITIC round):
```
## Adversarial Review — Summary (mode: <mode>)

**Status:** NOT VERIFIED — RESPONSE failed to complete this round

**Last round's findings:**
[Verbatim findings from the last successful CRITIC round]

---
**WARNING: This is NOT an approval. Manual review is required before merging.**
```

**Aborted** (no valid CRITIC review produced at any round, or manual abort):
```
## Adversarial Review — Summary (mode: <mode>)

**Status:** ABORTED — no valid review produced

**Diagnostic:** [what failed and where]

---
**No verified review exists. Re-invoke /dsar after resolving the diagnostic.**
```

**Operator summary** — built from per-round summaries already in the conversation (Step 5 verbatim reviews, Step 6.5 response summaries). Do NOT re-read `/tmp/dsar-*` files to compose the summary; Claude has the round-by-round output in context. If context compaction has trimmed the history, state the limitation explicitly instead of inventing details.

### Step 9 — Cleanup

Conditional on terminal state:

| Terminal state           | Cleanup |
|--------------------------|---------|
| Approved                 | Remove temp files |
| Maximum rounds reached   | Remove temp files |
| Not verified             | Remove temp files |
| Aborted                  | Leave in place    |

Plan Mode: skip cleanup entirely.

Outside Plan Mode, on a cleanup-eligible terminal state:

```bash
rm -f /tmp/dsar-plan-${REVIEW_ID}.md \
      /tmp/dsar-critic-body-${REVIEW_ID}.md \
      /tmp/dsar-response-body-${REVIEW_ID}.md \
      /tmp/dsar-review-${REVIEW_ID}.md \
      /tmp/dsar-response-${REVIEW_ID}.md
```

Do NOT delete plan files that existed before the review — only temp files this skill wrote.

The Step 8 deny-lock release runs BEFORE this rm — release is a correctness action, rm is hygiene.

## Enforcement (real, not skill-wording-only)

Skill wording alone does not stop Claude from calling `Read` or `Edit`. To actually block those tools on target paths, the skill writes `<REPO_ROOT>/.claude/.dsar-active-lock.json` and relies on a **user-level PreToolUse hook** (`~/.claude/hooks/dag-deny-check.sh`, registered in `~/.claude/settings.json`) that consults the lock on every `Read` / `Edit`. This is cwd-independent — fires even from a session launched from an unrelated directory.

### Pre-run — write the deny lock

Per Step 4.2.

### Post-completion — release the deny lock

At every terminal state (approved / max rounds / not verified / aborted):

1. Delete `<REPO_ROOT>/.claude/.dsar-active-lock.json`.
2. Emit `🔓 Active lock released.` to the operator.

If the delete fails (permissions issue, file already gone in a strange state), emit an operator warning with the manual-cleanup instructions from [Crash recovery](#crash-recovery).

### Crash recovery

If the session is interrupted after the lock is written but before Step 8's release, the deny lock stays in place and legitimate Claude Read/Edit on target paths fails in later sessions. Manual recovery:

```bash
cat <REPO_ROOT>/.claude/.dsar-active-lock.json    # confirm it's stale
rm  <REPO_ROOT>/.claude/.dsar-active-lock.json
```

On startup, the skill MUST check for a leftover `.dsar-active-lock.json` and prompt the operator for cleanup before starting any new review.

## Rules

- All code-touching actions run as DeepSeek workers on the Deck (`deck spawn`, workspace = `${REPO_ROOT}`); see the Deck dispatch override at the top. Native `Task` subagents and direct MCP are retired here. Direct `Edit` / `Write` on target paths by Claude is prohibited by wording AND by the active lock.
- Claude MUST NOT `Read` any file in `TARGET_PATHS` between the deny lock write (Step 4.2) and its release (Step 8). `Glob` / `LS` are permitted for enumeration.
- Round-0 = the user's existing changes / plan; DeepSeek does not rewrite from scratch. The loop opens at CRITIC.
- CRITIC and RESPONSE are separate MCP calls. Cross-call memory comes via `<prior_response>` in the round-N critic prompt (Step 7.1) and via reading the review file in the RESPONSE inputs (Step 6.1).
- Critic findings shown verbatim in Step 5; no rephrasing, no summarizing, no wrapping.
- Auto-detect review mode from context; explicit user arguments take priority.
- Maximum 5 rounds.
- Every terminal state: per-state header → operator summary in `OPERATOR_LANGUAGE` → Claude ship/no-ship verdict → deny-lock release.
- Cleanup conditional on terminal state.
- Operator language detected at Step 1. Machine-readable literals stay English.
- Deny lock enumerates every target path — no broad-glob shortcuts even when the list is long.
- If the Deck won't start → tell the operator `DeepSeek Deck not available — check $DEEPSEEK_DECK_HOME/bin/deck up and ~/.deepseek-mcp/config.json.` Do NOT fall back to Claude-direct or to Codex.
- CRITIC uses auditor tools (`Read,Glob,Grep,Bash,Write` — no `Edit`); RESPONSE gets the full editing set. One fresh worker per role per round.

## Anti-patterns

- Claude reading a target file "just to see what the critic found" — the deny lock will fire; the intent is prohibited regardless of the mechanism.
- Running the CRITIC or RESPONSE as a native `Task` subagent (or direct MCP) instead of a Deck worker — burns frontier tokens and loses the live panel.
- Letting a Deck worker write its review/response to `/tmp` — it's sandboxed to `${REPO_ROOT}`; artifacts go under `${REPO_ROOT}/.dsar/`.
- Merging into an existing `permissions.deny` array without a stale-lock check (silent overlap with a prior interrupted run).
- Leaving `.dsar-active-lock.json` in place past the terminal state.
- Rewriting the critic's verbatim content when displaying it in Step 5.
- Applying fixes Claude-side "for expediency" instead of dispatching RESPONSE.
- Collapsing an enumerated deny list to a broad glob "just because it's long" — trades precision for convenience.
- Attempting to use `codex exec` — this is the DeepSeek variant; Codex-era mechanics are removed on purpose.
