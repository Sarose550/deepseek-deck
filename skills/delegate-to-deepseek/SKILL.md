---
name: delegate-to-deepseek
description: Default heuristics for delegating batch, repetitive, or mechanical tasks (test writing, boilerplate, bulk refactors, log scans, ETL, single-endpoint CRUD, single-component impl, translations) to DeepSeek as a complete logical unit. Dispatch is via the DeepSeek Deck (`deck spawn`) so the operator sees a live, resumable panel at zero frontier-token cost per worker. Falls back to direct mcp__deepseek__delegate_to_deepseek for single quick one-shots. Skip when DEEPSEEK_MODE=off.
---

<!--
  Derived from the "delegate-to-deepseek" skill in PsChina/deepseek-as-subagent
  (https://github.com/PsChina/deepseek-as-subagent), MIT License, Copyright (c)
  2026 PsChina. Modified to dispatch via DeepSeek Deck. See ../../NOTICE.
-->

# delegate-to-deepseek (local override)

Overrides the upstream `deepseek-as-subagent` skill at
`~/Documents/deepseek-as-subagent/skills/delegate-to-deepseek/SKILL.md` so all
DeepSeek dispatches go through the **DeepSeek Deck** (`deck spawn`) for a live,
resumable, operator-visible panel — at zero frontier-token cost per worker.

## Dispatch rule (mandatory)

- **Preferred**: the **DeepSeek Deck** via `~/Documents/deepseek-deck/bin/deck spawn
  --task "<...>" [--workspace DIR] [--tools ...]` (see the `deck` skill). It runs
  the worker as a live, resumable panel in the Deck UI and returns only an id;
  collect the outcome with `deck result <id>` (compact). This spends **zero**
  frontier tokens per worker and is the default for all token-heavy delegation.
- **Fallback** (single quick one-shot, no UI/parallelism needed): direct
  `mcp__deepseek__delegate_to_deepseek(task=..., context=...)` or `/ds`.
- **Never** wrap DeepSeek work in a native `Task` subagent (e.g. `deepseek-runner`)
  — that spins up a metered frontier session per worker, the exact cost the Deck
  removes.

## When to delegate (heuristics)

Default: **delegate** for tasks with a clear success criterion that don't
require project-specific memory the main Agent alone holds. Good fits:

- Batch file modifications, bulk refactors, log scans, ETL, translations,
  test writing, boilerplate, single-endpoint CRUD, single-component impl,
  format-conversion, lint fixes.

Do NOT delegate:

- Tasks depending on `CLAUDE.md` / project-internal conventions the sub-agent
  can't see.
- Cross-domain architecture / tech-choice / ADR reasoning.
- Bug root-cause analysis (reasoning-heavy).
- Trivial edits (<200 lines, no file reads needed) — DeepSeek's reasoning
  start-up cost isn't recovered.
- When the user says "don't delegate" or "you do it".

## Delegation timing

Decide whether to delegate **before** reading source files. Once main-agent
context has ingested source, delegating means DeepSeek re-reads it and both
sessions pay the token cost.

Pre-delegation, use only `Glob`, `LS`, and read-only `Bash` (`ls`, `wc -l`,
`git status`) to size the task. Avoid `Read` and `Grep` on target files until
the delegate/don't decision is made.

## Pre-flight for external knowledge

DeepSeek is offline in the MCP sub-session. If the task needs external docs
(new API version, spec references, error-code lookups), use main-thread
`WebSearch` / `WebFetch` first, summarize into the `CONTEXT:` string.

## Task granularity

Ship complete logical units, not sub-steps. One delegation per feature or
per batch is usually right; splitting into 5+ sub-tasks per feature is
almost always more expensive than not delegating at all.

## After delegation

- Read a sample of produced files (`Read` a couple) to verify schema,
  file count, sanity. This is legitimate — the produced files are new
  artifacts, not source you were avoiding.
- If quality is bad, fix small issues yourself; re-delegate on scope drift;
  take over if DeepSeek gave up.

## Kill switches

- User says "you handle it" / "don't delegate" → skip DeepSeek for this turn.
- `DEEPSEEK_MODE=off` env var → skill is disabled for the session.
- MCP returns error twice in a row → main Agent takes over, warns operator.

## Upstream reference

The full upstream heuristics (in Chinese, with detailed cost math) live at
`~/Documents/deepseek-as-subagent/skills/delegate-to-deepseek/SKILL.md`.
That file is not loaded by Claude Code because this local override
supersedes it. Consult upstream when tuning cost-vs-quality tradeoffs.
