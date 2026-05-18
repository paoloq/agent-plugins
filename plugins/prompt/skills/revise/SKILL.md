---
name: revise
description: Revise an existing prompt against the official OpenAI, Anthropic, and Google prompt-engineering guides, walking the user through each change interactively. Use when the user says "revise this prompt" or asks to "refactor this prompt". Accepts inline prompt text, a file path, or a prompt-review JSON report path.
---

Refactor an existing prompt into a revised version, one small change at a time, each change anchored to a cited section of the official OpenAI, Anthropic, or Google prompt-engineering guides. The deliverable is a new file at `<HOME>/.claude/cache/prompt/revise/<project-slug>/<stamp>/<slug>-revised.md`. The original source is never modified in place.

Only revise. Drafting brand-new prompts belongs to the `draft` skill, and producing assessment reports belongs to `review`. If the source has a section the guides require but is missing, propose adding it — but the working surface stays "this prompt, made better," not "a fresh prompt on this topic."

## Token discipline

- Keep the source prompt, guides, and revised file out of chat — the user reads the file.
- Status lines describe the activity generically and use capitalized sentences — never name a specific guide id, provider, or model.
- Per revision turn = one line `✏️  <Section> · <Unit label>` + one `AskUserQuestion` call. Nothing else.
- Auto-skips (unchanged text, section not applicable) = one line `⏭️  <Section> · <Unit> — <Reason>`.
- Finalize prints one line: `🎉  <path>` — clickable working-file path, nothing else.

## Workflow

### 1. Resolve input

The user's input arrives in the **Input** section at the bottom of this file.

Auto-detect in this priority order:

1. **Report mode** — input is a path to an existing `.json` file whose top-level object has an `interventions` array. Read it. Then read the prompt at `prompt_path` (if present and the file exists) or fall back to `prompt_excerpt` (and note that revisions may be partial because only the excerpt is available). The revision walk iterates `interventions` ordered Critical → Info.
2. **File mode** — input is a path to any other existing file. Read it as the prompt to revise.
3. **Inline mode** — otherwise treat the input as the prompt text itself.
4. **Empty** — **session pickup first.** Scan the recent assistant messages in this conversation for a follow-up nudge emitted by the `review` skill (e.g. a `Next: revise <path>` line) or any bare path matching `<HOME>/.claude/cache/prompt/review/<project-slug>/<stamp>/.<slug>.json` printed earlier. If exactly one such path is present and the file still exists, ask the user one `AskUserQuestion`:
   - `header`: `♻️  Pick up report`
   - `question`: `♻️  Reuse this report from earlier? <path>`
   - options: `🛠️  Yes, revise it` / `No, I'll supply something else`.

   On `Yes`, treat that path as the input and proceed in **Report mode**. On `No` (or if no candidate path is found, or more than one is found), ask: `🛠️  Paste the prompt to revise, hand me a file path, or drop a review JSON.` If still empty, print `🙃  Nothing to revise yet — send a prompt, a path, or a report next time.` and stop.

If the input string both looks like an existing path *and* could be inline text, prefer the path interpretation. Only re-ask if no interpretation succeeds (file does not exist AND text is empty).

**Slug:** derive kebab-case from the prompt's apparent topic, or from the source filename minus extension, or — in report mode — from the report's `slug` field. Always suffix with `-revised`. Target path: `<HOME>/.claude/cache/prompt/revise/<project-slug>/<stamp>/<slug>-revised.md`. Resolve placeholders once at command start from system context: `<HOME>` and `<project-slug>` from cwd; `<stamp>` as `YYYYMMDD-HHMMSS` from today's date + current time (append `-2`, `-3`, … on the rare collision). Full cache-path convention: `<plugin-root>/references/paths.md`.

Use **Write** to create the working file (Write makes parent dirs). Print its path on its own line.

**Report mode — missing source prompt.** If the report's `prompt_path` is set but the file is gone, ask once via `AskUserQuestion` whether to (a) revise using only the `prompt_excerpt` (mark partial in chat), (b) supply a new path, or (c) cancel. Surface the missing source rather than degrading silently.

### 1.5. Clarify runtime context

Several prompt-engineering rules are conditional. Before revising, resolve the dimensions below. **For each dimension, first try to infer the answer from the prompt itself; only ask if it is genuinely silent or ambiguous.** Batch every unresolved dimension into a **single** `AskUserQuestion` call (max 4 sub-questions). If nothing is ambiguous, skip this step.

In **report mode**, prefer the `context` block already on the report payload over re-asking — only ask about dimensions the report left `unknown`.

Dimensions:

1. **Structured output** — JSON mode / `response_format` / tool-call as the output channel? If yes, skip free-form output-schema proposals and downgrade format-strictness revisions.
2. **Runtime user input** — does the prompt receive a user message at runtime, or is it self-contained? If self-contained, skip `Input schema` and `untrusted-input` clauses.
3. **Tools / agentic loop** — does the model call tools or loop? If no, skip `Tools`, `Stop rules`, and loop-cap clauses.
4. **Target model family** — `openai` / `anthropic` / `google` / `mixed`? Steers which guide weighs most on conflicts. Default `mixed` if unanswered.

For each unresolved dimension, propose two or three concrete options. On decline or "unsure", use conservative defaults (`runtime_user_input=yes`, `tools_or_agentic=no`, `target_model_family=mixed`).

### 2. Read the guides

`<plugin-root>` is the directory containing this plugin's `skills/` and `guides/` folders — derive its absolute path from this SKILL.md's own location (two directories up from `skills/revise/`). Use absolute literal paths; do not write `~` or `$HOME` (expanding them requires Bash).

Each guide lives in its own file under `<plugin-root>/guides/<id>.md`, with HTML comment markers at the top (`<!-- guide: <id> -->`, `<!-- provider: ... -->`, `<!-- models: ... -->`, `<!-- title: ... -->`, `<!-- url: ... -->`). Available guides: `openai-gpt-5-5`, `openai-gpt-5-4`, `anthropic-claude-4`, `google-gemini-3`.

Use **Read** on `<plugin-root>/guides/<id>.md` for each guide; page with `offset`/`limit` rather than loading entire long files. Use Read for file access — Bash calls (`grep`, `cat`, `head`, `sed`, `ls`) trigger permission prompts.

### 3. Plan the revision pass

Walk the source prompt section by section. For each section, identify the units that need revision, in this order:

- **Report mode** — start from the report's `interventions` (Critical → Info). For each intervention with a `suggested` field, that text is the seed revised wording for one unit; group interventions by the section they target. Then, after the report's interventions are walked, do a second light pass for any guide-anchored gap the report didn't surface (be sparing — the user asked for the report's revisions, not a rewrite).
- **File / inline mode** — walk the source's existing sections in order; within each, identify units (a sentence, clause, rule, example, schema field) whose wording materially conflicts with or falls short of guide-anchored best practice for the resolved runtime context.

For each candidate unit:

- If the proposed revised text is identical (case + whitespace insensitive) to the source, auto-skip with one line `⏭️  <Section> · <Unit> — No change needed`. Save the widget for changes that matter.
- If two guides give contradictory guidance for the same unit, surface both in the `AskUserQuestion` `question` field as one sentence (e.g. "OpenAI § X says A; Anthropic § Y says B") and let the user pick or supply `Other`. Never blend silently.
- If a wholly new section is needed (e.g. a missing `Stop rules` block flagged Critical), first ask one Approve/Skip `AskUserQuestion` for the section heading itself, then walk its units like any other section.

Seed the working file with the source prompt's full text up front (via `Write`), so each approved revision is an `Edit` that replaces the original unit in place. Approved insertions for new sections append at the right spot.

### 4. Section-by-section revision walk

For each unit, in order:

1. Print one line: `✏️  <Section> · <Unit label>` (e.g. `✏️  Goal · Tightening success bar`, `✏️  Constraints · Adding untrusted-input rule`).
2. Ask one `AskUserQuestion` with one sub-question:
   - `header`: ≤12-char tag (e.g. `Goal`, `Schema`, `Tools`).
   - `question`: the unit label and one sentence on what the unit changes. When the change is guide-anchored, append one short sentence naming the guide source (e.g. "Anchored on Anthropic's tool-use posture guidance, § X."). When two guides conflict on this unit, surface both in this sentence.
   - `multiSelect: false`.
   - `options`: exactly two — `Approve` and `Skip`.
     - `Approve.description` contains the proposed revised text verbatim, prefixed by `insert:` on its own line.
     - `Skip.description`: `Leave the original wording.`
3. On `Approve`: apply the change to `<HOME>/.claude/cache/prompt/revise/<project-slug>/<stamp>/<slug>-revised.md` via `Edit` (replace the matched original unit with the revised text; for net-new units, append at the right section).
4. On `Skip`: leave the original wording. Move on.
5. On `Other` (free-form): treat the user's text as feedback. Redraft the unit to address it and re-ask, **up to 3 revision rounds**. After the third round, treat the latest free-form text as the unit's content, apply it, and move on.

Move strictly forward through sections within a session — one forward pass. Further iteration is a fresh `revise` pass against the new file.

### 5. Finalize

After the last unit is handled:

1. Print the working path on its own line.
2. Stop. The file *is* the deliverable.

## Constraints

- Everything uses Read / Edit / Write. No Bash calls.
- Never invent a guide URL or section heading. Only the URLs in the guide-file markers and section headings visible inside `<plugin-root>/guides/<id>.md` are valid.
- Never edit the original source file (in file mode). Never edit anything outside `<HOME>/.claude/cache/prompt/revise/<project-slug>/<stamp>/<slug>-revised.md`.
- If the user later asks to assess the revised prompt, treat that as a separate task (hand off to the `review` skill); this skill only revises.
