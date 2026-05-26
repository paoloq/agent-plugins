---
name: loop
description: Auto-iterate the prompt review→revise cycle until a re-review surfaces no findings at or above the effort-selected severity floor. Use when the user says "loop this prompt", "iterate on this prompt", "iterate review and revise", or "keep revising until clean". Accepts inline prompt text, a file path, or a prompt-review JSON report path.
---

Orchestrate the existing `review` and `revise` skills in a tight loop. Each iteration runs `review` against the current prompt, filters interventions to the effort-selected severity floor, runs `revise` in auto-apply mode against those interventions, and asks the user whether to keep going. The deliverable is `<HOME>/.claude/cache/prompt/loop/<project-slug>/<stamp>/final-revised.md`, with every intermediate iteration kept on disk under `iter-NN/` for diffing.

## Token discipline

- Keep the source prompt, guides, review JSON, and revised file out of chat — the user reads the files.
- Never name a specific guide id, provider, or model in chat output.
- Per iteration prints exactly: one line `🔁  iter-NN · review`, the review's own brief output, one line `🔁  iter-NN · revise`, the revise auto-apply skip lines, and one gate `AskUserQuestion`.
- Finalize prints two clickable paths — `final-revised.md` and the latest `review.html` — then one `AskUserQuestion`.

## Effort levels

The effort arg picks a severity floor. Severities come from the `review` skill: `Critical | High | Medium | Low | Info`.

| Effort  | Addresses                                  |
| ------- | ------------------------------------------ |
| `low`   | Critical + High                            |
| `medium`| Critical + High + Medium                   |
| `high`  | Critical + High + Medium + Low *(default)* |
| `xhigh` | all, including Info                        |

## Workflow

### 1. Resolve input and effort

Auto-detect input in the same priority order as `review`:

1. **Report mode** — input is a path to an existing `.json` file with an `interventions` array. Skip iteration 1's review pass and use this report as iteration 1's review output.
2. **File mode** — path to any other existing file. Read as the prompt source.
3. **Inline mode** — otherwise treat the input as the prompt text itself.
4. **Empty** — ask once via `AskUserQuestion`: `🔁  Paste the prompt to loop on, hand me a file path, or drop a review JSON.` If still empty, print `🙃  Nothing to loop on yet.` and stop.

Resolve effort from a positional arg (`loop high <input>`). If absent, ask one `AskUserQuestion` with options `low`, `medium`, `high` (default-highlighted), `xhigh`. Map effort to severity floor per the table above. Record the chosen effort; it is reused across iterations unless the convergence prompt escalates it.

**Slug + stamp:** derive `<project-slug>` from cwd and `<stamp>` as `YYYYMMDD-HHMMSS`. The loop root is `<HOME>/.claude/cache/prompt/loop/<project-slug>/<stamp>/`. Full cache-path convention: `<plugin-root>/references/paths.md`.

### 2. Per-iteration flow

For iteration `N` (1-indexed, zero-padded to two digits as `NN`):

1. **Review.** Print `🔁  iter-NN · review`. Invoke the `review` skill with the current prompt source and `caller_out_dir = <loop-root>/iter-NN/`. The review writes `review.json` and `review.html` there and, because of the loop hook, suppresses its own open-report Bash call.
2. **Scope filter.** Read `<loop-root>/iter-NN/review.json` and keep interventions whose `severity` is at or above the effort floor.
3. **Stop check.** If the filtered set is empty:
   - If there are no out-of-scope findings either, jump to **Finalize** with `revised = current prompt source`.
   - Otherwise print `✅  iter-NN · converged at <effort>` and ask one `AskUserQuestion`: *"Converged at `<effort>`. Continue at the next level (`<next>`)?"* — options `Continue at <next>` and `Finalize`. On `Continue`, raise the floor one notch (`low→medium→high→xhigh`) and restart this iteration's filter step. On `Finalize`, jump to **Finalize**.
4. **Revise.** Print `🔁  iter-NN · revise`. Invoke the `revise` skill in auto-apply mode with `severity_floor = <current floor>`, `report_path = <iter-NN review JSON>`, and `caller_out_path = <loop-root>/iter-NN/revised.md`. Revise applies every in-scope intervention without per-change prompts and writes the revised file. That file becomes the prompt source for iteration `N+1`.
5. **Gate.** Print one line: `iter-NN · applied A · skipped S`. Ask one `AskUserQuestion` with options **Continue**, **Stop**, **Open last report**.
   - On **Continue**: advance to iteration `N+1`.
   - On **Stop**: jump to **Finalize** with `revised = iter-NN/revised.md`.
   - On **Open last report**: run one Bash `open <loop-root>/iter-NN/review.html`, then re-ask the gate with only **Continue** and **Stop**.
6. **Soft warning.** If `N >= 5`, prefix the gate message with `⚠️  N passes in — still finding issues. Consider stopping.` No hard cap.

### 3. Finalize

1. Copy the latest `iter-NN/revised.md` (or the original source, if iteration 1 stopped at the convergence prompt) to `<loop-root>/final-revised.md` using `Read` + `Write`.
2. Print two clickable lines: the `final-revised.md` path and the latest `review.html` path.
3. Ask one `AskUserQuestion` — options **Open final report**, **Done**. On **Open final report**, run one Bash `open` on the latest `review.html`. The Bash permission prompt is the only approval gate — never wrap it in an extra `AskUserQuestion`.

## Constraints

- At most one Bash call per iteration (`open` on the report), plus one Bash call at finalize. Everything else uses Read / Write and delegated skill invocations.
- Never invoke `review` or `revise` outside their loop hooks (`caller_out_dir` for review; `severity_floor` + `caller_out_path` for revise). Direct calls would land artifacts in the wrong cache root.
- Never mutate the original source file. The original is the input to iteration 1's review only.
- If the user later asks to apply additional manual revisions, hand off to the standalone `revise` skill; `loop` is the autonomous path.
