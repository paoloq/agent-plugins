---
name: draft-spec
disable-model-invocation: true
description: Draft a coding-task spec against a fixed 5-section template, asking the user only where recon leaves load-bearing ambiguity, then hand it off to implementation in the same session via ExitPlanMode. Use when the user says "draft spec", "write a spec", "/draft-spec", or asks to produce a SPEC document for a task.
---

You are drafting a task spec — a written `SPEC.md` at `./.specs/<slug>.md` that will be handed to implementation in the same session via `ExitPlanMode` once the user approves it.

Do not implement during the interview, do not echo section bodies in chat, and do not summarize recon. Ask the user only where recon leaves load-bearing ambiguity — never just to confirm what you already know.

## Token discipline

Every output token must be load-bearing.

- Never echo the spec or any section body in chat. Write to the file; the user reads it there.
- No preamble, no recap, no restating the user's answer, no "great, moving on".
- When you do ask, output one line `§N <Section name>` (or a shared header if the widget covers multiple sections) + the `AskUserQuestion` widget. Nothing else.
- Recon is silent. Do not summarize what you read.
- Do not narrate which sections you drafted silently — the user sees them in the file at finalize.
- Finalize prints only the clickable spec path, then calls `ExitPlanMode`. No executive summary, no how-to-open hints, no extra question widget.

## Workflow

### 1. Setup

1. Call `EnterPlanMode`.
2. Read the task from the **Input** section at the bottom of this file. If it's empty, ask once via `AskUserQuestion` ("What's the task?").
3. Derive a kebab-case slug from the task. Spec path: `./.specs/<slug>.md`. Do not create the file yet — it is written once after the interview completes.

### 2. Recon (silent)

Silently read whatever the codebase requires to ground all five sections — files matching the task domain, `package.json` scripts, existing test files, related modules. Read enough to draft confidently and to know where you genuinely can't. Never summarize the recon in chat.

### 3. Draft + ask only where needed

You have full discretion over which sections to draft silently and which to surface as questions. The default is: draft. Only ask when recon leaves a load-bearing ambiguity that a confident guess could get wrong in a way that breaks the implementing agent.

- For each of §1–§5, decide: is the right body obvious from the task + recon? If yes, draft it and hold it in memory. If no, prepare a question.
- Batch every question into a single `AskUserQuestion` call when possible — multiple sections can share one widget via sub-questions. Only fall back to multiple widget calls if a later answer genuinely depends on an earlier one.
- Options must be grounded in recon (real files, commands, constraints), per **Reference → Option-shape rules**.
- `AskUserQuestion` fields:
  - `question`: a short, specific question for that section (or sub-question).
  - `header`: the section name (≤12 chars; abbreviate). Use `N/M` for grouped sub-questions (e.g. `Src files 1/3`).
  - `multiSelect: true` for §3 Constraints and §4 Success criteria & verification (multi-bullet by nature); `false` elsewhere.
  - `options`: per Option-shape rules.
- On answer: hold the body in memory. If the user picks "Other" with free-form text, keep that text verbatim (lightly formatted as bullets if it reads like a list).
- If you didn't ask, your silent draft is the body.

Output rule when asking: one line `§N <Section name>` (or a combined header like `§2/§4` if the widget spans multiple) + the widget. Nothing else.

### 4. Finalize

Once every section has a body (drafted silently or answered):

1. If `./.specs/` does not exist, run `mkdir -p ./.specs`. `Write` the full spec to `./.specs/<slug>.md` in one call, assembling the template with each held section body in place.
2. Print one line: `🧾  Spec ready: ./.specs/<slug>.md:1` (use the exact `file_path:line_number` form so the TUI renders the path as a clickable link). Status lines are emoji-prefixed, capitalized, and describe the activity generically — never name a specific guide id, provider, or model.
3. Call `ExitPlanMode`. The spec file *is* the plan — `ExitPlanMode` reads it and prompts the user for approval natively. Pass `allowedPrompts` derived from §4 Success criteria & verification (e.g. `{tool: "Bash", prompt: "run tests"}`, `{tool: "Bash", prompt: "run lint and typecheck"}`) so common implementation actions are pre-authorized.

Branch on the user's response:

- **On approval**: continue in the same session and implement the spec at `./.specs/<slug>.md`. The spec is now the active implementation plan.
- **On rejection**: the user's free-form feedback indicates what to revise. Parse which section(s) it targets, ask only the questions needed to resolve the feedback (or just redraft if the feedback is concrete enough), update the held bodies, re-`Write` the full spec, re-print the `🧾  Spec ready: …` line, and call `ExitPlanMode` again.
- **On discard**: if the feedback is "discard" or equivalent, delete `./.specs/<slug>.md` and stop.

## Reference

### Template

Do not add sections beyond these. Every section is load-bearing and must be filled — either drafted silently from recon or resolved via a question.

```markdown
# <Task title>

## 1. Objective
<goal + WHY, one sentence>

## 2. Context
<specific files/dirs to reference, existing patterns to mirror, prior work, external links>

## 3. Constraints
<boundaries, allowed side effects, hard limits (perf, deps, compat, policy)>

## 4. Success criteria & verification
<concrete: commands to run, test cases (in→out), screenshots, lint/typecheck — what must be true before done>

## 5. Stop rules
<rules for the implementing agent: when to ask the user, when to fall back to a default, when to abstain from acting>
```

### Option-shape rules

The `AskUserQuestion` widget always offers an implicit "Other" — do not add one yourself. Pick the shape based on how many genuinely warranted options recon produces:

- **1 option**: present it as a confirmation — `Use this` (proposed body in the `description`) and `Different approach`. The implicit "Other" lets the user supply an alternative.
- **2–4 options**: a single question with those options.
- **>4 options**: partition them into **semantic groups** — each sub-question covers one orthogonal axis or dimension of the choice (e.g. for §2 Context: one sub-question for source files, one for tests, one for docs; for §4 Verification: one for unit tests, one for lint/typecheck, one for manual checks). Only fall back to an index-based split when no semantic axis exists. Each group holds 2–4 mutually-exclusive (or co-selectable, if `multiSelect`) options on its own axis; if a candidate group has only 1 option, fold it into a related group or drop it. Emit the groups as multiple sub-questions in **one** `AskUserQuestion` call (max 4 sub-questions = up to 16 options). If recon yields more axes than fit, prune to the 4 most load-bearing. Each sub-question gets a distinct `header` naming its axis (e.g. `Src files`, `Tests`, `Docs`).

## Constraints

- Only `./.specs/<slug>.md` is written.
- No web fetching, no model invocation.

## Input

$ARGUMENTS
