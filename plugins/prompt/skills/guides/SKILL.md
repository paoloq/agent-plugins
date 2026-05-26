---
name: guides
description: "Look up and explain prompt-engineering guidance from the official upstream documentation for OpenAI GPT-5.5, Anthropic Opus 4.7 / Sonnet 4.6, and Google Gemini 3 / 3.1, with section-level citations. Use this skill whenever the user asks 'what does the <provider> guide / docs say', says 'according to the <provider> docs/guide' or 'check the prompting guide', or otherwise asks for an authoritative answer about how to prompt one of these models. Skip for generic 'how should I prompt X' phrasing without an appeal-to-source, and skip model-selection / pricing / general coding questions. Do NOT use to assess or rewrite a specific user-supplied prompt — that is the `review` and `revise` skills."
model: "haiku"
---

# Prompt Guides

Answer prompt-engineering questions about the listed frontier models, grounded in the official upstream guides. Ground every substantive point in a specific guide section, and end the answer with a **Sources** block listing each cited section + URL once. Inline parenthetical URLs are not required as long as the section heading is named in-text and resolved in Sources.

## Scope

In-scope: prompt engineering for OpenAI GPT-5.5, Anthropic Opus 4.7 / Sonnet 4.6, Google Gemini 3 / 3.1.

Out of scope (refuse with one line): general coding help, model selection, pricing, anything else. Reply: `Out of scope for prompt-guides — try a general assistant or the relevant docs directly.`

## Trigger discipline

Only fire on explicit invocations: "check the prompting guide", "according to <provider> docs", "what does the <provider> guide say". Do **not** fire on generic "how should I prompt this" phrasing in passing.

## Layout

`<plugin-root>` is the directory containing this plugin's `skills/` and `guides/` folders. Derive its absolute path from this SKILL.md's own location (two directories up from `skills/guides/`). Use absolute literal paths; do not write `~` or `$HOME` (expanding them requires Bash, which triggers permission prompts).

Each guide lives in its own file under `<plugin-root>/guides/<id>.md`. Every file starts with HTML comment markers:

```
<!-- guide: <id> -->
<!-- provider: <provider> -->
<!-- models: <comma-separated list> -->
<!-- title: <human title> -->
<!-- url: <upstream url> -->
```

followed by the guide body. The available guides are:

- `openai-gpt-5-5` — openai — models: gpt-5.5
- `openai-gpt-5-4` — openai — models: gpt-5.4
- `anthropic-claude-4` — anthropic — models: claude-opus-4-7, claude-sonnet-4-6, claude-haiku-4-5
- `google-gemini-3` — google — models: gemini-3, gemini-3.1
- `cross-provider` — cross-provider — distilled consensus across all four guides above; use when the question is mixed-provider or provider-agnostic

## Workflow

For every invocation:

### 1. Pick the relevant guides

Match the user's question against the provider/models list above. Pick the guide ids the question concerns. If it spans multiple providers, or the target is unknown, read `cross-provider` first and add per-provider guides only for points where attribution matters.

### 2. Read the relevant guides

Use **Read** on `<plugin-root>/guides/<id>.md` for each selected guide. For long guides, page with `offset` / `limit` rather than loading the whole file at once.

Use **Read** for all file access — Bash calls (`grep`, `cat`, `head`, `sed`, `ls`) trigger permission prompts.

### 3. Answer

Answer with section headings named in-text (e.g., "the guide's *Plan before you act* section says…"), and end with a **Sources** block listing `<guide title> § <section> — <url>` once per cited section. Pull `title` and `url` from the markers at the top of each guide file.

### 4. Multi-provider answers

When the question spans multiple providers, structure the answer as:

- Start with a **Cross-provider consensus** section grounded in the `cross-provider` guide.
- One section per provider for points where the provider deviates from or extends the consensus, with section-heading references in-text.
- A single trailing **Sources** block resolving every cited section.

Keep provider-specific advice in its own section so attribution stays clear.

## Constraints

- All file access goes through Read on `<plugin-root>/guides/<id>.md`.
- Never invent a guide URL. Only the URLs in the guide-file markers are authoritative.
- Never fabricate a section heading. If a heading isn't visible in the guide file, cite the closest one that is, or say the guide doesn't cover the point.
