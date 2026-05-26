---
name: loop
description: Auto-iterate the prompt review→revise cycle until a re-review surfaces no findings at or above the effort-selected severity floor. Runs autonomously — no per-iteration confirmation prompts. Use when the user says "loop this prompt", "iterate on this prompt", "iterate review and revise", or "keep revising until clean". Accepts inline prompt text, a file path, or a prompt-review JSON report path.
---

Orchestrate the existing `review` and `revise` skills in a tight loop. Each iteration runs `review` against the current prompt, filters interventions to the effort-selected severity floor, runs `revise` in auto-apply mode against those interventions, and advances to the next iteration without asking the user. The loop is autonomous: it only stops on convergence, on the safety cap, or when revise applies nothing. The deliverable is `<HOME>/.claude/cache/prompt/loop/<project-slug>/<stamp>/final-revised.md`, with every intermediate iteration kept on disk under `iter-NN/` for diffing.

## Token discipline

- Keep the source prompt, guides, review JSON, and revised file out of chat — the user reads the files.
- Never name a specific guide id, provider, or model in chat output.
- Per iteration prints exactly: one line `🔁  iter-NN · review`, the review's own brief output, one line `🔁  iter-NN · revise`, the revise auto-apply skip lines, and one summary line `iter-NN · applied A · skipped S`. No gate `AskUserQuestion` between iterations.
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

Resolve effort from a positional arg (`loop high <input>`). If absent, ask one `AskUserQuestion` with options ordered `high`, `medium`, `low`, `xhigh` (first option is the recommended default; mark its label with `(Recommended)`). Map effort to severity floor per the table above. Record the chosen effort; it is reused across iterations unless the convergence prompt escalates it.

**Slug + stamp:** derive `<project-slug>` from cwd and `<stamp>` as `YYYYMMDD-HHMMSS`. The loop root is `<HOME>/.claude/cache/prompt/loop/<project-slug>/<stamp>/`. Full cache-path convention: `<plugin-root>/references/paths.md`.

### 2. Per-iteration flow

For iteration `N` (1-indexed, zero-padded to two digits as `NN`):

1. **Review.** Print `🔁  iter-NN · review`. Invoke the `review` skill with the current prompt source and `caller_out_dir = <loop-root>/iter-NN/`. For `N >= 2`, also pass `caller_context = <iter-01 review.json>.context` so review skips its runtime-context clarify step and reuses the dimensions resolved in iter-01 — without this, the loop loses autonomy on iter-2+. The review writes `review.json` and `review.html` there and, because of the loop hook, suppresses its own open-report Bash call. *Exception:* in **report mode** (§1.1), iter-01 skips the review call entirely — copy the seed JSON to `<loop-root>/iter-01/review.json` and proceed to step 2. Subsequent iterations always run review normally.
2. **Scope filter.** Read `<loop-root>/iter-NN/review.json` and keep interventions whose `severity` is at or above the effort floor.
3. **Stop check.** If the filtered set is empty:
   - If there are no out-of-scope findings either, jump to **Finalize** with `revised = current prompt source`.
   - Otherwise print `✅  iter-NN · converged at <effort>` and ask one `AskUserQuestion`: *"Converged at `<effort>`. Continue at the next level (`<next>`)?"* — options `Continue at <next>` and `Finalize`. On `Continue`, raise the floor one notch (`low→medium→high→xhigh`) and restart this iteration's filter step. On `Finalize`, jump to **Finalize**.
4. **Revise.** Print `🔁  iter-NN · revise`. Invoke the `revise` skill in auto-apply mode with `severity_floor = <current floor>`, `report_path = <iter-NN review JSON>`, and `caller_out_path = <loop-root>/iter-NN/revised.md`. Revise applies every in-scope intervention without per-change prompts and writes the revised file. That file becomes the prompt source for iteration `N+1`.
5. **Continue automatically.** Compute counters from the filtered intervention set, not by parsing revise's output: `A` = count of in-scope interventions handed to revise (everything at or above the floor); `S` = count of out-of-scope interventions (below the floor). Print one line: `iter-NN · applied A · skipped S`. Then advance directly to iteration `N+1` — do **not** ask the user. The loop is autonomous; the only natural stopping points are the convergence check (step 3) and the soft-warning cap (step 6). **Fixed-point guard:** if iter-NN's `revised.md` is byte-identical to its input prompt (revise auto-skipped every in-scope intervention as unchanged or not-applicable), jump to **Finalize** with that file. Compare via `Read` on both sides; this is the only way to detect the "revise applied nothing despite in-scope findings" case, since step 3's convergence check fires on filter emptiness, not on revise's actual edits.
6. **Soft warning / safety cap.** Define `SOFT_CAP = 8` and `HARD_CAP = 12`. The numbers are heuristics: by 8 passes a well-formed prompt is almost always converged, and 12 leaves a clear ceiling on cost if the user opts to push further. Track iterations. At `N == SOFT_CAP`, pause once and ask a single `AskUserQuestion`: *"N passes in — still finding in-scope issues. Keep going?"* with options **Continue to HARD_CAP**, **Finalize now**. On **Continue**, keep looping until `N == HARD_CAP`, then finalize unconditionally. On **Finalize now**, jump to **Finalize**. Below `SOFT_CAP`, never prompt.

### 3. Finalize

1. Copy the latest `iter-NN/revised.md` (or the original source, if iteration 1 stopped at the convergence prompt) to `<loop-root>/final-revised.md` using `Read` + `Write`.
2. Print two clickable lines: the `final-revised.md` path and the latest `review.html` path.
3. Ask one `AskUserQuestion` — options **Open final report**, **Done**. On **Open final report**, run one Bash `open` on the latest `review.html`. The Bash permission prompt is the only approval gate — never wrap it in an extra `AskUserQuestion`.

## Constraints

- No Bash calls during iteration. The only Bash call is one `open` at finalize, gated by the final `AskUserQuestion`. Everything else uses Read / Write and delegated skill invocations.
- The loop runs to convergence, to the safety cap (`N == 8`, then optionally 12), or to a no-op revise (`applied == 0`). Do not insert any other `AskUserQuestion` between iterations — that defeats the whole point of `loop`.
- Never invoke `review` or `revise` outside their loop hooks (`caller_out_dir` for review; `severity_floor` + `caller_out_path` for revise). Direct calls would land artifacts in the wrong cache root.
- Never mutate the original source file. The original is the input to iteration 1's review only.
- If the user later asks to apply additional manual revisions, hand off to the standalone `revise` skill; `loop` is the autonomous path.
