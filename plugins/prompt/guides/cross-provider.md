<!-- guide: cross-provider -->
<!-- provider: cross-provider -->
<!-- models: claude-opus-4-7, claude-sonnet-4-6, claude-haiku-4-5, gemini-3, gemini-3.1, gpt-5.4, gpt-5.4-mini, gpt-5.4-nano, gpt-5.5 -->
<!-- title: Cross-provider prompt-engineering guide -->

# Cross-provider prompt-engineering guide

Apply these rules when drafting, reviewing, or revising a prompt. Each item is directive. Snippets in fenced blocks are templates to insert into prompts; the rest is guidance to follow while writing.

## Foundations

- Assign the model a role in one or two sentences at the top of the system prompt. A focused role narrows tone and behavior more than scattered instructions can.
- State the goal, the audience, the constraints, and the output shape directly. Don't rely on the model to infer them.
- Avoid persuasive padding ("please", "if you don't mind") and vague qualifiers ("important", "thorough"). Replace them with concrete criteria.
- Include the rationale behind each non-obvious constraint. "Never use ellipses *because the output is read by TTS*" generalizes better than the bare rule.
- Place role definitions, behavioral constraints, and output-format requirements at the top of the system instruction or the user prompt.
- For long-context inputs, put the data first and the question last.
- Anchor the transition between data and question with "Based on the information above…".
- Match the prompt's style to the desired output. Markdown in → markdown out. Prose in → prose out.
- Instruct the model to skip "Here is the…" preambles and conversational openers ("Got it", "Great question").
- State requirements positively. Use "Write in flowing prose paragraphs", not "Do not use markdown".

## Structure: delimiters and sections

Use explicit delimiters for non-trivial prompts. Pick one convention per prompt — never mix XML tags and Markdown headings in the same prompt.

XML tags:

```xml
<role>…</role>
<context>…</context>
<instructions>…</instructions>
<constraints>…</constraints>
<examples><example>…</example></examples>
<output_format>…</output_format>
```

Markdown headings:

```markdown
# Role
# Goal
# Success criteria
# Constraints
# Output
# Stop rules
```

Wrap each input document and tag its metadata:

```xml
<documents>
  <document index="1">
    <source>…</source>
    <document_content>{{DOC}}</document_content>
  </document>
</documents>
```

For grounded QA over long documents, instruct the model to first extract relevant quotes into `<quotes>` before answering.

## Few-shot examples

- Include examples whenever format, tone, scoping, or edge-case handling matters. If examples are clear, you can drop most prose instructions.
- Use 3–5 examples. More risks overfitting.
- Make examples relevant, diverse, and consistently formatted. Inconsistent whitespace, tags, or delimiters across examples will leak into outputs.
- Wrap each in `<example>` (multiple in `<examples>`).
- Include a `<thinking>` block inside an example to demonstrate reasoning style — the model will generalize the pattern.

## Output control

### Verbosity and format

- Assume the default style is concise and direct. Explicitly request warmth, narration, or summaries if needed.
- Use the API's verbosity/length knob first, then refine with prompt instructions.
- Clamp list shape when output drifts to bullets: "no nested bullets", "use `1.` markers, never `1)`", "prefer prose unless comparing".
- For strict formats (JSON, SQL, XML): instruct *output only that format*, require bracket validation, and use the platform's structured-outputs feature instead of prompt-only schema enforcement.

### Style and tone

- Define **persona** (how it sounds: tone, warmth, formality) and **collaboration style** (when it asks vs. assumes, how proactive, how it handles uncertainty) separately.
- Specify channel (Slack / email / memo / PRD), emotional register, and a hard length cap.
- For editing/rewriting prompts: instruct *preserve length, structure, genre first; quietly improve clarity*. Forbid expansion of the artifact.

## Reasoning and effort

Treat reasoning/effort dials as last-mile tuning, not the primary quality knob — outcome contracts and verification loops recover more than raising effort.

- Prefer outcome contracts over step-by-step process scripts. Describe the destination; let the model pick the path unless the path matters.
- If the model over-explores, lower effort or add: *"Choose an approach and commit; revisit only on new contradicting information."*
- For complex problems, instruct a self-check: "Before finishing, verify your answer against [criteria]."
- Add explicit completion criteria, verification, and tool-persistence rules before raising effort.

## Tool use and agents

### Calling tools

- Be explicit about intent. "Can you suggest changes?" → suggests. "Make these edits." → acts.
- Choose default-to-action or default-to-ask and write it down:

  ```xml
  <default_follow_through_policy>
  - If intent is clear and the next step is reversible and low-risk, proceed.
  - Ask permission only if the step is irreversible, has external side effects,
    or requires missing sensitive info.
  </default_follow_through_policy>
  ```

- Put tool-specific guidance (what it does, when to use it, required inputs, side effects, retry safety, common errors) inside each tool's description. Put system-prompt tool guidance only when it applies across tools.
- Specify when to ask vs. proceed. Allow the model to proceed on reversible, low-risk steps with reasonable assumptions. Require a clarifying question only when the missing information would materially change the outcome or carry meaningful risk; require the question to be narrow.
- Avoid aggressive language. "CRITICAL: you MUST use this tool" tends to over-trigger. Use normal phrasing.

### Parallel tool calls

```xml
<use_parallel_tool_calls>
If multiple tool calls have no dependencies, issue them in parallel in a single
step. If a call's parameters depend on a prior call's output, sequence them.
Never use placeholders or guess parameters.
</use_parallel_tool_calls>
```

After a batch of parallel retrieval, pause to synthesize before issuing more calls.

### Completeness and persistence

```xml
<completeness_contract>
- Treat the task as incomplete until all requested items are covered, or
  explicitly marked [blocked] with the missing data named.
- Keep an internal checklist of required deliverables.
- For batches: determine expected scope, track processed items, confirm coverage.
</completeness_contract>

<empty_result_recovery>
If a lookup returns empty/partial/suspiciously narrow results, try at least one
fallback (alternate wording, broader filters, alternate source) before
concluding no results exist.
</empty_result_recovery>
```

### Verification before high-impact actions

```xml
<verification_loop>
Before finalizing or taking an irreversible action:
- Correctness: does the output satisfy every requirement?
- Grounding: are factual claims backed by provided context or tool outputs?
- Formatting: does the output match the requested schema?
- Safety: if the next step has external side effects, ask permission first.
</verification_loop>
```

### Agentic planning dimensions

For complex agents, instruct the model to reason across these dimensions before acting:

1. Logical dependencies — policy rules > order-of-operations > prerequisites > user preferences.
2. Risk assessment — low-risk exploratory reads vs. high-risk state changes.
3. Abductive reasoning — explore non-obvious causes; don't discard low-probability hypotheses prematurely.
4. Adaptability — revise the plan when observations contradict assumptions.
5. Information sources — tools, policies, prior observations, user.
6. Grounding — quote applicable rules when invoking them.
7. Completeness — exhaustive coverage of constraints/options.
8. Persistence — retry on transient errors with a limit; change strategy on other errors.

### Subagents

Use subagents for genuinely independent or context-isolated work. Forbid them for sequential single-file edits or simple lookups. If the model over- or under-spawns, write explicit conditions for when fan-out is warranted.

### Avoiding overengineering

```text
Avoid over-engineering. Make only changes directly requested or clearly
necessary. No docstrings on untouched code. No error handling for impossible
scenarios. No abstractions for one-time operations. Trust internal code; only
validate at system boundaries. Clean up any temporary files at the end.
```

## Grounding, research, and citations

- Lock claims to retrieved evidence: "Cite only sources retrieved in this workflow. Never fabricate citations, URLs, IDs, or quote spans."
- Attach citations to specific claims, not to a trailing bibliography.
- On conflicting sources: surface the conflict explicitly and attribute each side. Forbid averaging.
- On insufficient context: narrow the answer or state it cannot be supported. Forbid fabrication.
- Label inferences explicitly when they aren't directly supported.
- For strict grounding, instruct the model to answer only from provided context and forbid outside knowledge.
- Ground arithmetic, counting, and calculations through a code-execution tool when one is available — don't let the model compute numbers in prose.
- Ground unknown or potentially stale facts through a search/retrieval tool. Instruct the model to retrieve rather than recall when the answer depends on obscure, recent, or fast-changing information.

Research-mode template:

```xml
<research_mode>
1. Plan: list 3-6 sub-questions.
2. Retrieve: search each, follow 1-2 second-order leads.
3. Synthesize: resolve contradictions; write the final answer with citations.
Stop only when more searching is unlikely to change the conclusion.
</research_mode>
```

Retrieval budget template:

```text
Start with one broad search. Search again only when top results don't answer
the core question, a required fact/ID/date is missing, the user asked for
exhaustive coverage, or an unsupported factual claim would otherwise appear.
Do not search again merely to improve phrasing or add nonessential examples.
```

## Long context and state

- Long-context layout: data first, query last, one consistent tag scheme, metadata tags per document.
- For multi-window or long-running workflows: persist state to disk — git for change history, structured JSON for task tables (e.g. `tests.json`), freeform `progress.txt` for notes. When resuming in a fresh context window, instruct: "review `progress.txt`, `tests.json`, and `git log`; run the smoke test before resuming."
- For prompt caching: keep stable content at the top of the request and dynamic per-user content at the bottom.

## Safety, irreversibility, autonomy

Include this block whenever the model can act on external systems:

```text
Consider the reversibility and impact of actions. Local, reversible actions
(editing files, running tests) are fine to take. For actions that are hard to
reverse, affect shared systems, or are destructive, confirm with the user
first.

Examples that warrant confirmation: deleting files/branches, dropping tables,
`rm -rf`, `git push --force`, `git reset --hard`, amending published commits,
sending messages, posting to external services, modifying shared infrastructure.

When encountering obstacles, never use destructive actions as a shortcut.
Do not bypass safety checks (e.g. --no-verify) or discard unfamiliar files
that may be in-progress work.
```

## Mid-conversation instruction updates

Make updates scoped, explicit, and reversible. State scope (this turn / rest of conversation), override (what changes), and carry-forward (what still applies). User instructions override defaults; newer user instructions override older ones; safety and privacy never yield.

```text
<task_update>
For the next response only:
- <override>
All earlier instructions still apply unless they conflict with this update.
</task_update>
```

## Iteration

- Vary phrasing when output is wrong — wording shifts outputs even when semantics don't.
- Switch to an analogous task framing when the model resists — recast "categorize" as "multiple-choice question".
- Try different content orders — examples before context, or input before examples.
- Change one variable at a time — model, effort, prompt — and re-run evals between changes.
- For review/extraction prompts, beware qualitative quality filters. "Only report important issues" causes recall to drop; instead instruct "report every finding with confidence + severity; a separate step will filter."

## Suggested skeleton

Keep each section short; expand only where it changes behavior.

```xml
<role>
[1-2 sentences: who the model is, what it's for]
</role>

<personality>
[Tone, formality. When to ask vs. assume. Proactivity level.]
</personality>

<goal>
[User-visible outcome — describe the destination, not every step.]
</goal>

<success_criteria>
[What must be true before the final answer.]
</success_criteria>

<context>
[Data, documents, prior state. For long context, place this near the top.]
</context>

<constraints>
[Policy, safety, evidence rules, side-effect limits.]
</constraints>

<tools>
[When to call, when to parallelize, when to stop. Most tool detail lives in
the tool descriptions themselves.]
</tools>

<examples>
  <example>…</example>
</examples>

<output_format>
[Format, length, sections. Use structured outputs for strict schemas.]
</output_format>

<stop_rules>
[When to retry, fall back, abstain, ask, or stop.]
</stop_rules>
```

Or the Markdown-headings equivalent — pick one convention per prompt:

```markdown
# Role
# Personality
# Goal
# Success criteria
# Context
# Constraints
# Tools
# Examples
# Output
# Stop rules
```

## Anti-patterns

| Anti-pattern | Why it fails | Do instead |
|---|---|---|
| Stacked negatives ("Do X. Don't do Y. Never Z.") | Pattern-matches the negative concept | State the positive directly, then show an example |
| "CRITICAL: YOU MUST use the search tool" | Over-triggers | "Use the search tool when …" |
| Prescriptive step-by-step on simple tasks | Narrows the search space; mechanical answers | Describe the outcome; let the model pick the path |
| Mixing XML tags and Markdown headings | Inconsistent structure parsing | Pick one and stick with it |
| Putting long documents at the bottom | Worse long-context retrieval | Data first, question last |
| "Be thorough" as a quality lever | Triggers over-exploration | Add a `<verification_loop>` and a `<completeness_contract>` |
| Qualitative filter bars ("only important findings") | Obeyed too literally; recall drops | "Report every finding with confidence + severity; a later step filters" |
| Describing a full JSON schema in the prompt | Drift, parse failures | Use the platform's structured-outputs feature |
| Raising effort to fix a poorly-specified prompt | Cost/latency up, gains marginal | Add outcome contract, completion criteria, verification first |
