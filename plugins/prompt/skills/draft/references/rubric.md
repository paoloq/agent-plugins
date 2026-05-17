# Draft rubric

Plugin-specific scaffolding for the section walk: fixed section order, per-section skip rules, and the units the drafter proposes inside each section. Cross-cutting writing rules that are already covered by the official prompt-engineering guides are not restated here — invoke the `guides` skill on the relevant provider instead.

## Section order (fixed)

Role → Personality → Goal → Success criteria → Input schema → Instructions → Constraints → Tools → Output → Stop rules → Examples.

## Section rules

1. **Role**
   - *Skip when* Discovery audience-and-style is "deterministic back-office" and the brief implies a generic transform, classifier, or extractor with no identity beyond "do this task."
   - **Identity** — one sentence naming the agent's identity and primary capability.

2. **Personality**
   - *Skip when* Discovery audience-and-style is "deterministic back-office" or "developer-tooling" with no multi-turn interaction — pure transforms, classifiers, extractors, or back-office automation where only correctness matters.
   - **Tone** — warmth, directness, formality, humor; one or two adjectives, not a paragraph.
   - **Collaboration style** — how proactive to be, when to ask vs. proceed, how to handle uncertainty, how much context to volunteer. Distinct from tone: tone is voice, collaboration style is working posture. Worth specifying for any agent that takes initiative or interacts across multiple turns.

3. **Goal**
   - *Skip only when* the goal is fully captured by the Role sentence.
   - **Target outcome** — in concrete terms.
   - **Outcome-shaping constraints** — anything that bounds the result without prescribing procedure.

4. **Success criteria**
   - *Skip when* the goal sentence already implies the bar (deterministic transforms, simple classifiers).
   - **Acceptance bar** — an enumerated `success means: …` list, one bullet per check.
   - **Quality bar** when correctness alone isn't enough — tone, depth, calibration, or stylistic criteria.

5. **Input schema**
   - *Include iff* Discovery runtime-shape is "multi-turn agent" or "extractor-or-classifier" **and** the runtime user message has internal structure the agent must parse.
   - **Shape contract** — describe the fields, types, and required-vs-optional status of what will arrive in the user message. Wrap the structure in `<input_schema>` or a fenced code block so the model reads it as data, not instructions.
   - **Field semantics** — one line per non-obvious field naming what it means and any value constraints.
   - **Missing/extra-field handling** — what to do when required fields are absent or unknown fields appear.
   - This describes the *shape* of the incoming message; the message itself arrives at runtime and is never drafted here.

6. **Instructions** — propose as bullet points, one rule per bullet, not prose paragraphs.
   - *Skip only when* the procedure is fully obvious from the goal (a one-step transform).
   - **Procedure** — outcome-first by default: state the destination and the constraints, and let the model choose the path. Reserve explicit step-by-step recipes for cases where the exact sequence is genuinely required (order-sensitive workflows, regulatory procedures, pipelines with strict dependencies).
   - **Scope** — what's in, what's out.
   - **Edge-case handling.**
   - **Rationale** for non-obvious constraints.

7. **Constraints** — propose as bullet points, one prohibition per bullet.
   - *Skip when* the agent's domain has no refusal concern, no untrusted user input, no secrets in scope, and no editing or grounded-generation work.
   - **Refusal pattern** — short, non-judgmental — when the domain warrants refusal.
   - **Untrusted-input rule** — when the prompt accepts runtime user input.
   - **Secrets rule** — no echoing of secrets or system framing.
   - **Preservation clause** (editing / rewriting / summarizing tasks).
   - **Anti-hallucination clause** — *Include iff* Discovery flagged grounding-or-RAG.
   - **Rationale** for non-obvious prohibitions.
   - Procedural scope (what's in/out of the task) belongs in §6, not here.

8. **Tools**
   - *Include iff* Discovery capabilities includes "tools".
   - **Positive trigger conditions** — when to call each tool.
   - **Parallel vs. sequential rules.**
   - **Confirmation gates** on destructive actions.
   - **Default action posture** as a named clause — *act-first* or *advise-first*. Consult the `guides` skill for the target provider's specific phrasing.
   - **Streaming preamble** (streaming or tool-heavy tasks).
   - **Context-persistence behavior** for long-running agents — when to compact, when to checkpoint, plus an explicit "save state and continue" rule against early stopping.
   - Retrieval/evidence budgets belong in §10 *Stop rules*, not here.

9. **Output**
   - *Skip only when* the output shape is fully obvious from the goal.
   - **Explicit format** — schema, headings, length cap.
   - **Strict output schema** — *Include iff* Discovery capabilities includes "structured-output". Consult the `guides` skill for the provider's strict-schema syntax.
   - **Audience** when the output is editorial, customer-facing, or written for a specific reader level.
   - **Verbosity control.**
   - **Failure-mode handling** — what to do with missing, ambiguous, or out-of-scope input.
   - **Self-check** — lightweight, before final output, when the task is non-trivial.
   - **Voice and surface form** that mirror the desired output style.
   - **Citation behavior** — *Include iff* Discovery flagged grounding-or-RAG. Which claims need citation, in what format, and what to do when evidence is missing. Absence of evidence must not auto-default to a factual "no."
   - **Creative vs. cited** (generative drafting — slides, leadership blurbs, outbound copy, narrative framing).
   - **Quote-grounding** (long-document tasks) — instruct the model to first extract relevant excerpts into a `<quotes>` block from the embedded documents and base its answer on those quotes.

10. **Stop rules**
    - *Skip when* the task is a one-shot transform with no retry, fallback, or abstain decision.
    - **Retry/fallback** — when to retry a tool or strategy, when to switch, when to give up.
    - **Abstain conditions** — when to refuse to answer rather than guess.
    - **Ask vs. proceed** — when missing information warrants a clarifying question vs. proceeding on a stated assumption.
    - **Retrieval/evidence budgets** — e.g. "use the minimum evidence sufficient to answer correctly, then stop."
    - **Loop caps** — explicit maximum iterations on retries to prevent runaway tool calls.

11. **Examples**
    - Default to including 2–5 diverse examples whenever output format, tone, or judgment calls are non-trivial. Consult the `guides` skill for the target provider's recommended example structure and tag conventions.
    - *Skip only when* the task is a deterministic one-step transform whose I/O is fully captured by a one-line prose description.
    - Propose each example as its own insertion unit; cover edge cases and vary enough that the model doesn't latch onto an unintended pattern.

## Cross-cutting quality bar

Plugin-specific application notes only. For the underlying prompt-engineering rationale, invoke the `guides` skill on the relevant provider.

- **Imperatives are imperative.** Every proposed unit reads as an instruction to the downstream agent ("Change this function", not "could you suggest changes").
- **Specificity matches sensitivity.** Maximally precise for extraction/classification units; shape-and-constraints for open-ended synthesis units.
- **Define jargon up front.** If a unit references domain terminology, named heuristics, or shorthand the downstream agent may not share, propose a defining unit before the unit that uses it.
- **Positive framing over prohibitions.** When proposing Instructions or Constraints units, reach for the positive rephrase first; fall back to a prohibition only when the desired behavior cannot be described positively. *(Anchored in all three guides — see `guides` on negative-instruction handling.)*
- **State scope explicitly when a rule should generalize.** Modern instruction-following models do not silently extend a rule from one item to all items. If a unit's rule should apply broadly, the unit text says so.
- **Decision rules over absolute rules** for judgment calls. Reserve ALWAYS / NEVER / MUST for true invariants: safety rules, required output fields, actions that must never happen.
- **Markdown spine, XML for embedded structure (never mix).** Markdown headings (`## Role`, `## Goal`, …) are the section spine — always. XML is reserved for these four in-section purposes:
    - **Data boundaries** — `<documents>` / `<document>` / `<source>`, `<context>`, `<schema>`, `<input>`.
    - **Examples** — `<example>` inside `<examples>`; identical field order, whitespace, and separators across examples.
    - **Named policy blocks** — `<default_to_action>`, `<refusal_policy>`, etc.
    - **Output format indicators** — `<thinking>` / `<answer>`, `<quotes>` / `<info>`, `<plan>` / `<result>`.
  Tiny prompts skip scaffolding entirely. *(For provider-specific tag conventions, consult `guides`.)*
- **Long-context assembly: data first, query last.** When the prompt embeds large bodies of context, propose the context block above the instructions and place the actual query at the end, with an explicit anchor phrase bridging them. *(Load-bearing across all three guides.)*
- **Explain the *why* behind non-obvious constraints.** When proposing a constraint or instruction whose motivation isn't self-evident, include the rationale in the same unit.
- **No filler.** No preamble, no apology theater, no politeness padding in any proposed unit.
- **No redundancy or contradiction.** Resolve conflicts as they arise during the walk; never leave the model to reconcile.
- **No manual chain-of-thought.** Reasoning depth is steered through native effort/thinking parameters, not prose padding. *(See `guides` on thinking budgets per provider.)*
