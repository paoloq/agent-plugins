---
name: review
description: Assess a user-supplied prompt against the official OpenAI, Anthropic, and Google prompt-engineering guides and emit a ranked list of interventions as a navigable HTML report. Use when the user says "review this prompt" or asks to "assess this prompt".
---

Review prompts against the official upstream prompt-engineering guides from OpenAI, Anthropic, and Google, and produce a ranked list of concrete interventions. The deliverable is a self-contained HTML report at `<HOME>/.claude/cache/prompt/review/<project-slug>/<stamp>/<slug>.html`; the user opens it in a browser to triage findings.

Only assess. Rewrites are a separate task — hand them off to the `revise` skill, even if asked in the same turn.

## Token discipline

- Keep the prompt, guides, and report body out of chat — the user reads the file.
- Status lines are at most one line each, emoji-prefixed and describe the activity generically — never name a specific guide id, provider, or model (e.g. `📖  Reading guides`, `🧪  Assessing prompt`, `📝  Drafting interventions`).
- On completion, print one line: `🧾  <path>` — clickable HTML path, nothing else.

## Workflow

### 1. Resolve input

The user's input arrives in the **Input** section at the bottom of this file.

- Empty → ask once via `AskUserQuestion`: `🔍  Paste the prompt to review, or hand me a file path.` If the answer is still empty after that, print `🙃  Nothing to review yet — drop a prompt or a path next time.` and stop.
- Existing file path → read the file. Use it as the prompt to review.
- Inline text → use as the prompt to review.

Derive a kebab-case slug from the prompt's apparent topic (or the source filename minus extension). Target directory: `<HOME>/.claude/cache/prompt/review/<project-slug>/<stamp>/`. Resolve placeholders once at command start from system context: `<HOME>` and `<project-slug>` from cwd; `<stamp>` as `YYYYMMDD-HHMMSS` from today's date + current time. If a directory with that stamp already exists (very rare), append `-2`, `-3`, … Full cache-path convention: `<plugin-root>/references/paths.md`.

### 1.5. Clarify runtime context

Several prompt-engineering rules are conditional — applying them blindly produces false-positive interventions. Resolve the four dimensions below before assessing.

**Default behavior: ask the user.** For each dimension, ask unless the prompt **explicitly** names the answer using the literal signals listed below. Soft inference ("seems like…", "probably…", "the structure suggests…") is treated as ambiguous — when in doubt, ask. Batch every dimension that needs asking into a **single** `AskUserQuestion` call (max 4 sub-questions).

Dimensions (each line says when to **skip the question** and record the explicit value; otherwise, include it in the batched ask):

1. **Structured output** — skip and record `yes` when the prompt text literally contains one of: `JSON mode`, `response_format`, `output_schema`, an `<output>` tag with a schema inside, the phrase `respond in JSON only`, or a code-fenced JSON schema. Otherwise ask.
2. **Runtime user input** — skip and record `yes` when the prompt text literally contains one of: `$ARGUMENTS`, `<user_input>`, `<user_message>`, `<query>`, or the phrase `the user will ask`. Skip and record `no` when the prompt text literally contains data inlined under a `<data>` / `<document>` / `<context>` tag with no separate runtime channel. Otherwise ask.
3. **Tools / agentic loop** — skip and record `yes` when the prompt text literally names tools (a `## Tools` section, a `<tools>` block, `tool_use` references, or a tool inventory). Skip and record `no` when the prompt is a single-shot transform with no looping or stop-rule language. Otherwise ask.
4. **Target model family** — skip and record the family when the prompt text literally names a specific model (`gpt-5.5`, `claude-opus-4-7`, `gemini-3`, etc.). Otherwise ask.

For each asked dimension, propose **two or three concrete options** (the obvious yes / no / one nuance), plus the implicit "Other" the user can use to clarify. Record the final answers verbatim in a `context` object on the report payload (see step 4). When the user picks an unsure option or declines, use conservative defaults (`runtime_user_input=yes`, `tools_or_agentic=no`, `target_model_family=mixed`) and note that in the `context.notes` field.

### 2. Read the guides

`<plugin-root>` is the directory containing this plugin's `skills/` and `guides/` folders — derive its absolute path from this SKILL.md's own location (two directories up from `skills/review/`). Use absolute literal paths; do not write `~` or `$HOME` (expanding them requires Bash).

Each guide lives in its own file under `<plugin-root>/guides/<id>.md`, with HTML comment markers at the top (`<!-- guide: <id> -->`, `<!-- provider: ... -->`, `<!-- models: ... -->`, `<!-- title: ... -->`, `<!-- url: ... -->`). The available guides are:

- `openai-gpt-5-5` — openai — title `OpenAI GPT-5.5 prompting guide`
- `openai-gpt-5-4` — openai — title `OpenAI GPT-5.4 prompting guide`
- `anthropic-claude-4` — anthropic — title `Anthropic Claude 4 best practices`
- `google-gemini-3` — google — title `Google Gemini Prompt design strategies`

Use **Read** on `<plugin-root>/guides/<id>.md` for each guide; page with `offset`/`limit` rather than loading entire long files. Use Read for file access — Bash calls (`grep`, `cat`, `head`, `sed`, `ls`) trigger permission prompts.

### 3. Assess

Walk the prompt against each guide, **filtered by the runtime context resolved in step 1.5**. Raise an intervention only when the runtime context keeps the rule in play (e.g. skip "missing Output schema" when structured output is already on). For each remaining issue or improvement opportunity, draft one **intervention** with:

- `severity`: one of `Critical | High | Medium | Low | Info`.
  - **Critical** — the prompt will likely fail or produce unsafe/incorrect output for its stated goal.
  - **High** — a documented best practice is materially violated and the model's behavior will suffer.
  - **Medium** — guidance suggests a clearer path; output quality is at risk.
  - **Low** — a minor refinement supported by the guides.
  - **Info** — neutral note (e.g. "you could additionally do X"), no defect implied.
- `title`: one-line headline naming what to change.
- `rationale`: 1–3 sentences saying why this matters *for this specific prompt*, not generic restatement.
- `suggested` (optional): concrete rewrite or addition, kept short.
- `citations`: at least one entry, each with `provider`, `title` (guide title), `section` (section heading from the relevant guide file — never invented), and `url` (from the guide's `<!-- url: -->` marker). Every intervention must cite at least one specific guide section.

Be honest. If the prompt is good, return few interventions (or only Info). Keep the list tight.

### 4. Render the report

Build the JSON payload:

```json
{
  "prompt_title":   "<short kebab slug or filename>",
  "slug":           "<slug>",
  "prompt_path":    "<original path if a file was supplied, else empty>",
  "prompt_excerpt": "<first ~500 chars of the prompt>",
  "context": {
    "structured_output":   "yes|no|unknown",
    "runtime_user_input":  "yes|no|unknown",
    "tools_or_agentic":    "yes|no|unknown",
    "target_model_family": "openai|anthropic|google|mixed|unknown",
    "notes":               "<optional one-line summary of assumptions or how each value was resolved>"
  },
  "guides":         [<one entry per guide read from guides/<id>.md markers: {provider, title, url}>],
  "interventions":  [<one entry per finding, ordered Critical → Info>]
}
```

Use **Write** to put the payload at `<HOME>/.claude/cache/prompt/review/<project-slug>/<stamp>/.<slug>.json` (Write creates parent directories). Then run the bundled renderer via Bash to produce `<slug>.html` next to the JSON:

```bash
python3 <plugin-root>/skills/review/scripts/render_report.py --project <project-slug> --stamp <stamp> --slug <slug>
```

The script resolves the input/output paths from the static base `$HOME/.claude/cache/prompt/review/` plus the three args — no full paths needed.

Keep the JSON in place after the HTML lands — the `revise` skill consumes it in report mode (it reads the `interventions` array and the recorded `prompt_path`). Print the HTML path on its own line as `🧾  <HOME>/.claude/cache/prompt/review/<project-slug>/<stamp>/<slug>.html`.

Then attempt to open the report directly via Bash — the tool's own permission prompt is the single approval gate; do **not** add an `AskUserQuestion` on top of it:

```bash
open <HOME>/.claude/cache/prompt/review/<project-slug>/<stamp>/<slug>.html
```

If the user denies the Bash call, stop without retrying. Either way, before stopping, print **exactly two** follow-up lines so the user can act on the report without retyping a path. Substitute the real slug and timestamp.

```
🛠️  Want to apply the fixes? Ask me to revise it and I'll dive right in.
   Explicit form: revise <HOME>/.claude/cache/prompt/review/<project-slug>/<stamp>/.<slug>.json
```

Informational only — leave the command for the user to fire when they want to.

## Constraints

- At most two Bash calls per session: `scripts/render_report.py` after writing the JSON, and `open` on the final HTML (the Bash permission prompt is the only approval gate — never wrap it in an extra `AskUserQuestion`). Everything else uses Read / Write.
- Never invent a guide URL or section heading. Only the URLs in the guide-file markers and section headings visible inside `<plugin-root>/guides/<id>.md` are valid.
- If the user later asks to apply the rewrites, treat that as a separate task; this skill only assesses and reports.
