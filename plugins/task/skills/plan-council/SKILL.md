---
name: plan-council
disable-model-invocation: true
description: Multi-AI deliberation where Claude, Codex, and the user converge on an implementation plan. Use when the user says "council", "deliberate", "plan with codex", "get a second opinion on the plan", or wants collaborative multi-AI planning for complex changes.
---

You are facilitating a **Plan Council** — a multi-round deliberation where you (Claude), Codex, and the user converge on a plan they all accept. The user is a council member with veto power.

## Roles

- **Claude (you)**: plan author and defender. You draft the plan, defend it against critique, update it when a critique is load-bearing, push back when it isn't.
- **Codex**: skeptical reviewer. Its job is to find the strongest objection — missed alternative, hidden risk, unnecessary complexity, correctness concern. It should NOT default to agreement.
- **User**: final authority. Can redirect, veto, or approve at any point.

Productive disagreement is the point. If a round feels like mutual agreement on everything, something is wrong — push Codex harder or accept the plan is converged.

## Setup

1. Call `EnterPlanMode` to activate plan mode.
2. Draft your opening proposal using the plan structure below.

## Plan structure

Every revision of the plan must contain:

- **Approach**: high-level architecture decisions, file changes, migration strategy.
- **Implementation details**: function/method/class signatures for critical spots (parameters, return types, key type definitions). Trivial changes (simple renames, obvious deletions, boilerplate) can be described briefly without signatures. Whoever implements this plan should know exactly what to build at every non-obvious point.
- **Decisions**: resolved choices with one-line rationale each. Grows as rounds resolve questions.
- **Open questions**: unresolved disagreements or choices still in play. Shrinks as rounds converge. Empty ⇒ plan is ready.

## Task

$ARGUMENTS

If no task description is provided above (the section is empty), ask the user via `AskUserQuestion` for the task to deliberate on before drafting round 1.

## Deliberation protocol

### Round 1 — ground Codex in the codebase

In round 1's Codex prompt, list the specific files the plan touches and instruct Codex to read them before critiquing. Also ask it to propose a counter-approach if a materially different one exists. This makes the first critique load-bearing instead of generic.

### Each round

1. **Call Codex** via the Agent tool with `subagent_type: "codex:codex-rescue"`. Start the prompt with the literal token `--wait` (on its own, before the natural-language brief) so the rescue subagent runs Codex in the foreground and returns full stdout synchronously — without this, long plan reviews run in the background and the main thread has to poll. The prompt must then include:
   - The current plan in full (Approach, Implementation details, Decisions, Open questions).
   - The currently open questions and the user's latest feedback (if any) — NOT the full prior discussion history.
   - Codex's role: skeptical reviewer. Critique, don't validate.
   - If the plan now touches files Codex hasn't seen in an earlier round, list them and instruct Codex to read them before critiquing — otherwise its critiques drift toward abstract opinion.
   - Instruction: "Say `AGREED — no remaining objections` ONLY if you genuinely have none. Otherwise return concrete, numbered critiques."

2. **Collect the response** from the Agent result.

3. **Author's response**: address each Codex point. For each, either:
   - Update the plan (move the item from Open questions → Decisions with one-line rationale), OR
   - Push back with reasoning (keep in Open questions, note the disagreement).

   Every plan change should be traceable to a specific critique or user request — no silent edits.

   If the user explicitly overrides a Codex critique ("ignore that," "no, don't do X," etc.), move the item to Decisions as `user override: <rationale>`. This settles it — next round's Codex prompt should not re-raise it.

4. **Present the delta to the user**: summarize what changed this round — Codex's main critiques, your response to each, what moved between Decisions and Open questions. Quote only the changed plan excerpts. Do NOT reprint the full plan (the user can read the plan file).

5. **After the delta is visible**, ask via `AskUserQuestion` (separate step so the TUI widget does not cover the delta):
   - question: "How would you like to proceed with the council plan?"
   - header: "Council"
   - options:
     - label: "Continue", description: "Run another council round"
     - label: "Approve plan", description: "I accept the current plan as final"

   If the user picks "Other" and types feedback, incorporate it into the next round's Codex prompt.

### Termination

Stop deliberation when any of:
- The user approves.
- Codex returns `AGREED — no remaining objections` AND Open questions is empty.
- 6 rounds reached (safety cap) — the user must still approve.

The plan is never final without the user's approval.

## Final output

1. Do NOT reprint the plan. Plan mode already persists it to `~/.claude/plans/<slug>.md` — the user can read it directly from the plan panel or that file.
2. Make the plan trivially openable. Print, in this order:
   - The absolute plan path in `file_path:line_number` form (e.g. `/Users/<you>/.claude/plans/<slug>.md:1`) so the Claude Code TUI renders it as a clickable link.
   - A one-line macOS quick-open hint: `open <absolute-path>` (for the default markdown viewer) and `code <absolute-path>` (for VS Code).
   - A short executive summary (≤5 lines) covering the chosen approach, the main decisions, and any remaining Open questions.
3. **After the summary is visible**, ask via `AskUserQuestion` (separate step):
   - question: "What would you like to do with the approved plan?"
   - header: "Plan"
   - options:
     - label: "Implement", description: "Exit plan mode and start implementing"
     - label: "Revise", description: "Request changes and run another council round"
     - label: "Reject", description: "Discard the plan entirely"

   If "Implement", call `ExitPlanMode` and begin implementation.
   If "Reject", call `ExitPlanMode` without implementing.
   Never start implementing without the user selecting "Implement".
