---
name: draft
description: Draft a prompt from scratch, section by section, asking the user only guide-anchored or detail-anchored questions. Use when the user says "draft a prompt" or "write a prompt", or supplies an idea or brief for a new prompt.
---

Draft prompts from scratch, interactively. A **prompt** here is the **system instruction** submitted to an AI agent (an LLM) at runtime — it tells the agent what role to play, what to do, and what to produce. It is **not** a human-facing brief, spec, or README, and it is **not** the user's live request. The user's live request and any runtime reference material arrive in a separate user message at runtime; never draft those. Every proposed unit must be addressed to the downstream agent in the second person ("you do X", "your output is Y"), in imperative, machine-actionable language — no prose explaining the prompt to a human reader, no meta-commentary about the prompt's purpose.

Whatever the user supplies in this session is a **brief or idea** — a description of what the eventual prompt should make the agent do. Do not edit existing prompts and do not formalize a user-supplied prompt; if the input reads like an already-written prompt, treat it as a brief and draft a fresh one anyway. The output is always a new file at `<HOME>/.claude/cache/prompt/draft/<project-slug>/<stamp>/<slug>.md`, built section by section, with each small insertion approved by the user before it lands.

## Provider guides are the source of truth

When proposing a unit whose best practice depends on the target provider — XML scaffolding, structured outputs, tool-use posture, long-context order, thinking budgets, citation patterns, refusal style, example structure — invoke the `guides` reference on the relevant provider(s) and ground the wording (and any one-line rationale offered) in the cited section. The embedded rubric is a default starting point; on conflict with an official guide, the guide wins. Never cite a guide section not read in this session.

## Question discipline

Every question asked of the user — in Discovery and during the section walk — must be one of:

- **Guide-anchored** — it maps to a dimension one of the official prompt-engineering guides explicitly treats as load-bearing (e.g. Anthropic's structured XML scaffolding, OpenAI's strict structured outputs, Gemini's data-first/query-last ordering, every guide's stance on tool-use posture, refusal, grounding). State the anchor in the question's header chip or one-sentence framing.
- **Detail-anchored** — a concrete fact about the user's task that a section genuinely cannot be drafted without (the runtime input shape, the tool inventory, the target audience for a customer-facing answer, the canonical output schema).

Do not ask anything else. Do not ask for aesthetic preference on prompt-engineering choices — that judgment belongs to the drafter. Do not ask a question whose answer can be inferred from the brief or from earlier answers.

## Token discipline

- Keep the working file and long passages out of chat — the user reads the file.
- Status lines describe the activity generically and use capitalized sentences — never name a specific guide id, provider, or model.
- Per insertion turn = one line `✏️  <Section> · <Unit label>` + one `AskUserQuestion` call. Nothing else.
- Section auto-skips = one line `⏭️  <Section> — <Reason tied to Discovery or brief>`.
- Finalize prints one line: `🎉  <path>` — clickable working-file path, nothing else.

## Workflow

### 1. Setup

1. Resolve the input from the **Input** section at the bottom:
   - A free-form brief or idea → use as the seed.
   - Already-written prompt → treat the text as a brief describing the same goal; draft fresh.
   - Empty → ask once via `AskUserQuestion`: `✍️  What should this prompt make the agent do?` with options `Describe it` (free-form via Other) and `Cancel`. Cancel stops the session.
   - Clearly not a prompt brief (random code, bare URL, question to you) → reply with one line `🤔  That doesn't look like a prompt brief — nothing to draft. Send a one-liner about what the agent should do?` and stop.
2. Derive a kebab-case slug from the brief's apparent topic. Working path: `<HOME>/.claude/cache/prompt/draft/<project-slug>/<stamp>/<slug>.md`. Full cache-path convention: `<plugin-root>/references/paths.md`.

   Use **Write** to create the empty working file (Write makes parent dirs).
3. Print the working path on its own line so the TUI renders it as a clickable link.

### 2. Discovery

Before drafting any section, run **one** `AskUserQuestion` call with up to four sub-questions. Pick the four most load-bearing facets for *this* brief; skip a facet entirely when the brief already pins it. Standard facets, each guide-anchored:

- **Target provider/model** — *Anchor: every guide is provider-specific; XML vs JSON scaffolding, thinking budgets, structured-output syntax, and tool-use posture all differ.* Determines which `guides` lens applies during drafting. Options: OpenAI / Anthropic / Google / cross-provider. Pick `cross-provider` when the prompt must run on more than one provider or the target is unknown — drafting will then anchor on the cross-provider guide.
- **Runtime shape** — *Anchor: all three guides distinguish one-shot transforms from multi-turn agents; instruction depth, stop rules, and tool sections depend on it.* Options: one-shot transform / multi-turn agent / extractor-or-classifier.
- **Capabilities** — *Anchor: structured outputs (OpenAI strict schemas, Anthropic output tags, Gemini response_schema), tool use (every guide has an explicit posture rule), grounding/RAG (Anthropic long-context, OpenAI Responses API), each unlock or require specific sections.* Multi-select: structured-output / tools / grounding-or-RAG / none-of-these.
- **Audience and style** — *Anchor: every guide treats audience and verbosity as primary levers shaping Role, Personality, and Output.* Options: deterministic back-office / customer-facing / creative-editorial / developer-tooling.

After answers land, record them as one line each in the working file inside a temporary `<!-- discovery: … -->` HTML comment block at the top, so the user can spot wrong answers immediately. Then proceed to the section walk. The Discovery answers drive every later skip/include decision.

Ask **one** additional detail-anchored follow-up immediately before drafting a section if and only if the section cannot be drafted without that detail (for example, "What fields arrive in the user message?" before Input schema, when Discovery flagged structured runtime input and the brief did not describe its shape). Never ask aesthetic preferences.

### 3. Section-by-section draft

Walk the skeleton in order:

1. Role
2. Personality
3. Goal
4. Success criteria
5. Input schema
6. Instructions
7. Constraints
8. Tools
9. Output
10. Stop rules
11. Examples

For each section:

- Apply the section's skip rule against the Discovery answers and the brief. Auto-skip with one line `⏭️  <Section> — <reason>`. Continue.
- Otherwise, write the section heading (`## <Section>`) to the working file via `Edit`, then propose the section's content as **small insertion units** — one rule, clause, sentence, or example per unit. Units must be short enough to fit readably inside an `AskUserQuestion` option's `description` field. Long blocks (multi-paragraph examples, full schemas) must be split.

For each insertion unit, in order:

1. Print one line: `✏️  <Section> · <Unit label>` (e.g. `✏️  Goal · Success criteria`, `✏️  Tools · Act-first posture`).
2. Ask one `AskUserQuestion` with one sub-question:
   - `header`: ≤12-char tag (e.g. `Role`, `Schema`, `Tools`).
   - `question`: the unit label and one sentence on what the unit adds. When the unit is provider-specific, append one short sentence naming the guide source (e.g. "Anchored on Anthropic's tool-use posture guidance.").
   - `multiSelect: false`.
   - `options`: exactly two — `Approve` and `Skip`.
     - `Approve.description` contains the proposed text verbatim, prefixed by `insert:` on its own line.
     - `Skip.description`: `Leave this out.`
3. On `Approve`: append the unit's text to the section in `<HOME>/.claude/cache/prompt/draft/<project-slug>/<stamp>/<slug>.md` via `Edit`. Move on.
4. On `Skip`: omit the unit. Move on.
5. On `Other` (free-form): treat the user's text as feedback. Redraft the unit to address it and re-ask, **up to 3 revision rounds**. After the third round, treat the latest free-form text as the unit's content, append it, and move on.

When all units of a section are handled, move to the next section. Do not re-open earlier sections in the same session. One forward pass.

### 4. Finalize

After the last section is handled:

1. Strip the `<!-- discovery: … -->` block from the working file.
2. Print the working path on its own line.
3. Stop. The file *is* the deliverable.

## Reference

The drafter's section-walk rubric — fixed section order, per-section skip rules, the units that belong in each section, and plugin-specific notes on the cross-cutting quality bar — lives in `references/rubric.md`. Read it before the section walk and consult it as each section comes up.

For prompt-engineering rationale underlying the rubric (positive framing, data-first/query-last ordering, XML scaffolding conventions, thinking budgets, strict-output syntax, example structure), invoke the `guides` skill on the relevant provider rather than restating guidance. On conflict between rubric and guide, the guide wins.

## Skill scope

- Only `<HOME>/.claude/cache/prompt/draft/<project-slug>/<stamp>/<slug>.md` is written. No other files are modified.
- Provider guidance is consulted via the `guides` skill — no other web fetching, no model invocation.
