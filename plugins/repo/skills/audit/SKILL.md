---
name: audit
disable-model-invocation: true
description: Assess a repository's readiness for agentic coding by running static checks, dispatching four benchmark subagents on the parent session's model, and reconciling exact token usage from local session logs into a navigable HTML report under ./.repo-audit/. Use when the user says "agentic readiness", "assess agentic readiness", "check this repo for agent readiness", or "/repo:audit".
---

You are the **orchestrator** for an agentic-readiness assessment of the current repository. You drive a single Python CLI through three calls — `prepare`, `attribute`, `finalize` — and dispatch four benchmark subagents in between. The CLI owns every file; you write nothing yourself.

## First-run permission note

The only Bash prefix this skill emits is:

```
${CLAUDE_PLUGIN_ROOT}/skills/audit/scripts/audit.py …
```

The script ships with the executable bit set and runs directly via its `#!/usr/bin/env python3` shebang — so Claude Code's "Always allow" attaches to this exact script path, **not** to a broad `python3 *` rule. Approve it once; every subsequent run is silent and no other python invocations are affected. The Agent tool spawns the benchmark subagents — those are tool calls, not Bash. Subagents run unisolated (no `isolation: "worktree"`) because they're read-only by contract, so no `git` / `rm` commands escape from the orchestrator either.

## Artifacts

Everything lives under `./.repo-audit/` in the user's repo:

```
./.repo-audit/
  runs/<iso-ts>/
    report.html        ← the user opens this
    summary.json       ← small, durable, history-diffable
  latest -> runs/<iso-ts>
```

`prepare` creates `runs/<iso-ts>/tmp/` for working files; `finalize` deletes that subdirectory once the HTML is rendered. There are no other files anywhere — no root sidecars, no stray JSON.

## Token discipline

- No preamble, no recon narration, no progress chatter. Run tools; emit the final summary.
- Final chat output is exactly the three or four lines shown at the bottom of this file — nothing else.
- The HTML report is the durable artifact; never duplicate its contents in chat.

## Workflow at a glance

Two script calls bracket the run; the orchestrator's job is everything in between.

1. **Prepare** — one CLI call returns `run_id`, the static checks (including `walkability` + `targets.locate_*`), and any `CLAUDE_CODE_SUBAGENT_MODEL` override.
2. **Dispatch** — eight benchmark subagents in parallel; collect their session ids from the completion notifications.
3. **Read final messages** — for each subagent, read the final message text and judge `specificity` (`high|medium|low`). For the Locate subagent, also compute `path_match` by comparing its stated path against `targets.locate_answer_path`. These per-task qualitative scores feed the Walkability pillar.
4. **Finalize** — one CLI call. Pass the session ids and the matrix JSON (with `specificity` per task and `path_match` for Locate). The script reconciles usage from the session transcripts, renders the HTML, writes `summary.json`, and prunes intermediates.
5. **Reply** — three or four lines, ending with the explicit hand-off.

### 1. Prepare

```bash
${CLAUDE_PLUGIN_ROOT}/skills/audit/scripts/audit.py prepare
```

Parse the JSON on stdout. Keep `run_id` for every later call; read `static` to inform the qualitative judgement; if `env_override` is non-null, record a warning verbatim:

> `CLAUDE_CODE_SUBAGENT_MODEL=<value> is set in your env — subagents were forced onto that model regardless of the orchestrator. To benchmark on the orchestrator's model instead, remove the env entry from ~/.claude/settings.json (under "env") and restart Claude Code.`

That env var wins over the Agent tool's `model:` parameter and over subagent frontmatter — Claude Code routes subagents to it silently. The warning lets the report explain why `actual_model` may not match `orchestrator_model`.

### 2. Benchmark dispatch (parallel)

Dispatch **eight** subagents — one per task — concurrently via the `Agent` tool. Use `subagent_type: "general-purpose"` and `run_in_background: true`. **Do not** pass `isolation: "worktree"` — subagents are read-only by contract (no `Edit`/`Write` permission rule, prompt suffix forbids them) so worktree isolation buys no real containment and costs an extra permission surface plus per-dispatch cleanup.

**Set `model:` to the alias matching your own family.** The Agent tool's schema only accepts `"sonnet"` / `"opus"` / `"haiku"` and does not honour `"inherit"`. Omitting the parameter defaults to Sonnet — which silently downgrades a benchmark run on Opus. Resolve your own model id (you know it from the runtime, e.g. `claude-opus-4-7`) and pass the matching alias:

- `claude-opus-*` → `model: "opus"`
- `claude-sonnet-*` → `model: "sonnet"`
- `claude-haiku-*` → `model: "haiku"`

That keeps every subagent on the orchestrator's family. After dispatch, verify by reading `usage.sessions[*].model` in the finalize output — if it doesn't match your `orchestrator_model`, add a warning to the matrix.

Wall-clock budget ≈ 5 minutes per task.

For each completion notification, record the **session id** — that's the only thing `finalize` needs.

**Do not sleep-poll for subagents.** Dispatch with `run_in_background: true` and let the harness deliver the completion notifications — they arrive automatically when each subagent finishes. Never insert `sleep N && …`, manual retry loops, or output-file polling between dispatch and `finalize`; the harness blocks long sleeps and chained-short-sleep waits. If you genuinely need to gate on a non-task condition, use `Monitor` with `until <check>; do sleep 2; done`. Otherwise just wait — you'll be notified.

**This is a simulation, not a hands-on benchmark.** Subagents read the repo and *describe* the change they would make as a unified diff in their final message — they never call `Edit` or `Write`. The subagent **does not pick its own target** — the orchestrator substitutes the concrete file/symbol from `prepare`'s `targets` field into each prompt, so every task is fully specified before dispatch. That makes the four tasks deterministic per repo, always exercisable (no `no-candidate`), and comparable across repos.

Append a per-task suffix to every prompt. Description-only tasks (Repo walk, Locate, Trace, Spot) get the **description** suffix; the four edit-shaped tasks end in a unified diff and get the **diff** suffix.

- **Description suffix** (Repo walk, Locate, Trace, Spot): `"Do NOT call Edit or Write. Do NOT run tests, linters, formatters, or any verification commands. Output the answer in your final message and stop."`
- **Diff suffix** (Bug fix, Feature add, Refactor, Write a test): `"Do NOT call Edit or Write. Do NOT run tests, linters, formatters, or any verification commands. Output the proposed change as a single unified diff in your final message and stop."`

Subagents that try to edit anyway will hit a permission prompt and stall — record them as `incomplete: budget exceeded`.

`prepare`'s `targets` object has these fields, all relative to the repo root:

- `deepest_file` — deepest-nested text file (used by **Bug fix**).
- `entry_point` — best-guess top-level entry (`main.py`, `cli.ts`, `__main__.py`, …); fallback to `largest_file` (used by **Feature add**).
- `largest_file` — heaviest text file by bytes (used by **Refactor**).
- `symbol_in_largest_file` — a renameable symbol from `largest_file`: prefers `_`-prefixed, falls back to any def/class/function name, then any identifier (used by **Refactor**). Practically always non-null on a real repo.
- `test_target` — the largest existing test file (under `tests/`, `test/`, `__tests__/`, `spec/`, …); `null` if the repo has no test directory (used by **Write a test**). If `null`, substitute `<entry_point>` and tell the subagent to invent a sibling `*_test.*` file path.
- `symbol_in_test_target` — a renameable identifier picked from inside `test_target` (typically an existing test function name to mirror); `null` when `test_target` is `null` (used by **Write a test**).
- `locate_concept` — a plain-English concept handed to the **Locate** subagent (e.g. ``"the file that defines the symbol `SessionTotals`"``). `null` on tiny repos with no qualifying symbol — record `skipped: no candidate` for Locate in that case.
- `locate_answer_path` — the correct file path for `locate_concept`. The orchestrator never reveals this to the subagent; it only uses it to compute `path_match` after the subagent answers.

Templated prompts. Replace `<…>` placeholders with the corresponding `targets` value before dispatch. Wording outside the placeholders is fixed — do not edit it.

1. **Repo walk** — "List every top-level directory of this repository. For each, give a one-line purpose. Then flag every pair of directories whose purposes overlap, duplicate each other, or are ambiguous about which one owns what (e.g. `utils/` vs `shared/`, `configs/` vs `env_files/`). Be concrete — name the dirs and explain the overlap in one sentence each. Do not write architectural prose; output two sections: `## Directories` (one line each) and `## Overlaps` (one bullet each, or 'none found')."
2. **Locate a concept** — "Without using any prior knowledge of this repository, identify <locate_concept>. Output exactly one repo-relative file path on its own line, followed by a ≤3-line justification citing the lines or text that make this file the unambiguous owner. If you cannot find a single owner, output `UNKNOWN` and explain in one sentence what made the search ambiguous."
3. **Trace an entry point** — "Starting from `<entry_point>`, list the chain of in-repo modules that participate in the *first user-visible action* this entry point performs (e.g. parsing args, handling the first HTTP request, running the first task). Maximum 8 hops. Output one bullet per hop in the form `path/to/file.ext :: function_or_section — one-line role`. Stop when you reach an external dependency or the chain genuinely terminates. If the entry point's first action is unclear, say so in one line and stop."
4. **Spot dead / generated / duplicated** — "Identify every top-level directory or file that looks (a) generated, (b) duplicated/overlapping with another, or (c) dead/unused. For each, give one line: `path — category — one-sentence reason`. Categories must be exactly `generated`, `duplicate`, or `dead`. Output 'none found' if the repo is clean. Do not flag standard project files (`README.md`, `LICENSE`, `pyproject.toml`, etc.)."
5. **Bug fix** — "Open `<deepest_file>` and treat it as if a reviewer flagged a subtle bug in it (off-by-one, unhandled error path, misleading log message, contradicting docstring — pick whichever shape best fits the file's actual content). Produce a minimal unified diff that plausibly fixes that imagined bug. This is a simulation — the bug does not need to be real, but the diff must be syntactically valid against the current file content."
6. **Feature add** — "Open `<entry_point>` and produce a unified diff that adds one small leaf feature there: a new no-op CLI flag, a new utility function with a single accompanying test, or an additional case in an existing dispatch table — whichever is the most natural fit. Keep the diff under 30 lines and idiomatic for the file's language."
7. **Refactor** — "Open `<largest_file>` and produce a unified diff that renames the symbol `<symbol_in_largest_file>` to a clearer name. Update every in-file call site of that symbol; if the symbol has no in-file call sites (i.e. it's only declared and referenced externally), the diff is allowed to be a single-line declaration rename — produce it anyway, since this is a simulation of refactor latency, not a guarantee of internal-rename breadth. Do not change any public API."
8. **Write a test** — "Open `<test_target>` and produce a unified diff that adds one new test next to the existing test/fixture `<symbol_in_test_target>`, exercising another behavior of whatever that test covers. Mirror the assertion style, fixture pattern, and naming of `<symbol_in_test_target>` exactly. The new test must be importable as written — do not reference symbols outside `<test_target>`'s current import scope. If `<test_target>` is `null` (no test directory exists), instead open `<entry_point>` and produce a diff that creates a sibling `*_test.*` file with one test for any obvious behavior in `<entry_point>`, using whichever test runner the project's `tests` static checks already identified."


### 3. Finalize

After all five subagents finish (or hit budget), author the matrix JSON in-message (see schema below) and call finalize once. The CLI parses each session's JSONL transcript (exact, model-reported counts — not heuristic), renders the HTML, and prunes `tmp/`. Per-session `status` ends up `ok`, `not-found`, or `schema-unrecognized`; never fabricate counts on a miss.

```bash
${CLAUDE_PLUGIN_ROOT}/skills/audit/scripts/audit.py finalize \
    --run <run_id> \
    --sessions <sid1> <sid2> <sid3> <sid4> <sid5> <sid6> <sid7> <sid8> \
    --matrix-json '<compact JSON>'
```

`finalize` renders `report.html`, writes `summary.json`, and deletes the `tmp/` directory. It prints one JSON object including `report_path` and `warnings`.

### Repo shape

`prepare` returns `static.repo_shape.shape`, either `"code"` (default) or `"agent-harness"`. The shape selects which pillar set to author in the matrix. The renderer reads the same field and switches its section list to match — author the wrong set and the report will render a pillar with no data.

- **`code`**: traditional application repo. Pillars: Repo profile, **Walkability**, Instructions, **Agent config**, Tests, Hygiene, Dev environment, Observability, Security, Benchmark, Cost.
- **`agent-harness`**: plugin marketplace, skill library, prompt collection, eval suite — no app code to deploy, so Tests/Dev env/Observability don't apply. Pillars: Repo profile, **Walkability**, Instructions, **Agent config**, **Evals**, Hygiene, **Skill quality**, **Prompt hygiene**, Security, Benchmark, Cost.

Shape detection runs on five signals (`has_plugin_manifest`, `has_skill_md`, `has_evals_dir`, `markdown_dominant`, `no_top_build_manifest`); `agent-harness` triggers at ≥3.

Matrix JSON schema (the **only** thing you author yourself). Pick the `scores[]` block that matches `repo_shape.shape`:

```json
{
  "orchestrator_model": "claude-opus-4-7",
  "actual_model":       "claude-sonnet-4-6",
  "grade":              "A|B|C|D",
  "rationale":          "one-line summary",
  "scores": [
    // code shape:
    {"label": "Repo profile",   "grade": "A", "tone": "ok|warn|bad"},
    {"label": "Walkability",    "grade": "B", "tone": "warn"},
    {"label": "Instructions",   "grade": "B", "tone": "warn"},
    {"label": "Agent config",   "grade": "C", "tone": "bad"},
    {"label": "Tests",          "grade": "A", "tone": "ok"},
    {"label": "Hygiene",        "grade": "A", "tone": "ok"},
    {"label": "Dev environment","grade": "B", "tone": "warn"},
    {"label": "Observability",  "grade": "C", "tone": "bad"},
    {"label": "Security",       "grade": "B", "tone": "warn"},
    {"label": "Benchmark",      "grade": "B", "tone": "warn"},
    {"label": "Cost",           "grade": "A", "tone": "ok"}
    // agent-harness shape — swap Tests/Dev env/Observability for:
    // {"label": "Evals",          "grade": "B", "tone": "warn"},
    // {"label": "Skill quality",  "grade": "A", "tone": "ok"},
    // {"label": "Prompt hygiene", "grade": "B", "tone": "warn"},
  ],
  "benchmark": [
    {"task": "Repo walk",    "session_id": "<sid>", "wall_clock_s": 24.8, "outcome": "completed", "specificity": "high"},
    {"task": "Locate",       "session_id": "<sid>", "wall_clock_s": 18.1, "outcome": "completed", "specificity": "high",  "path_match": true},
    {"task": "Trace",        "session_id": "<sid>", "wall_clock_s": 27.6, "outcome": "completed", "specificity": "medium"},
    {"task": "Spot",         "session_id": "<sid>", "wall_clock_s": 21.0, "outcome": "completed", "specificity": "high"},
    {"task": "Bug fix",      "session_id": "<sid>", "wall_clock_s": 25.3, "outcome": "completed", "specificity": "high"},
    {"task": "Feature add",  "session_id": "<sid>", "wall_clock_s": 22.3, "outcome": "completed", "specificity": "high"},
    {"task": "Refactor",     "session_id": "<sid>", "wall_clock_s": 16.8, "outcome": "completed", "specificity": "high"},
    {"task": "Write a test", "session_id": "<sid>", "wall_clock_s": 19.4, "outcome": "completed", "specificity": "high"}
  ],
  "recommendations": ["…"],
  "warnings":        ["…"]
}
```

Rules:

- Token counts and USD live in `usage.json` — never duplicate them in the matrix; the renderer joins on `session_id`.
- `orchestrator_model` is your own model (e.g. `claude-opus-4-7`); fill it in verbatim.
- `actual_model` is whatever the subagents' session logs report (`claude-sonnet-4-6` is typical because Claude Code routes subagents to its default model regardless of the orchestrator).
- Use the exact `tone` strings `ok` / `warn` / `bad`.
- The eight `benchmark[].task` names are fixed: `Repo walk`, `Locate`, `Trace`, `Spot`, `Bug fix`, `Feature add`, `Refactor`, `Write a test`. One entry per task.
- `benchmark[].specificity` is one of `high|medium|low`, authored by you after reading the subagent's final message. `high` = concrete names + correct count + no hedging. `medium` = partially specific, some hedging or omissions. `low` = vague generalities, missed obvious items, or filler. This grade feeds the **Walkability** pillar.
- For the `Locate` entry only, add `path_match: true|false` based on whether the subagent's stated path equals `targets.locate_answer_path` (case-sensitive, exact). When the subagent answered `UNKNOWN` or no path, set `path_match: false`. When `targets.locate_concept` is `null`, omit the Locate entry entirely and add a recommendation that the repo is too small for the Locate benchmark.
- At most 5 recommendations, ordered by impact.
- **Walkability grading** (reads `static.walkability` + `static.agent_config.has_codebase_map` + the four description-shaped benchmark tasks): start at `A`. Drop one notch for each of these:
  - `static.walkability.signals_score >= 2` (the static signals — root dir count, duplicate-named dirs, generated dirs at root, prefix collision — agree the repo is hard to scan). **Exception**: if `static.agent_config.has_codebase_map == true`, do not drop — an explicit map (ARCHITECTURE.md / STRUCTURE.md / a README with a top-level dir map) compensates for a noisy layout. Recommend a map instead of renames when this signal alone would have triggered.
  - `Repo walk` specificity is `medium` or `low` (the model couldn't commit on per-dir purposes or missed overlaps).
  - `Locate` `path_match == false` *or* the task was skipped for budget (the model couldn't locate an unambiguous owner).
  - `Trace` specificity is `low` *or* `outcome != "completed"` (entry-point flow is too tangled to follow in 8 hops).
  - `Spot` specificity is `low` (the model couldn't name the dup/generated/dead items the static signals already flagged).
  Cap the pillar at `bad` if `signals_score == 4` *and* at least two task specificities are `low`. Surface recommendations naming the specific duplicate pairs, generated dirs, and overlap dirs from `walkability` — do not generalise.
- **Instructions grading** (reads `static.agent_instructions` + `static.agent_config.nested_instructions`): the pillar passes when at least one of `CLAUDE.md` / `AGENTS.md` is present at the root (`agent_instructions.at_least_one_present == true`). Only `mirror_parity == "missing-both"` is a `bad` score. `claude-only` or `agents-only` is `ok` — surface a recommendation to mirror the other file, but do not penalise. **Quality probes** (cap the pillar at `warn` and emit one targeted recommendation each):
  - A present root file with `over_line_limit == true` — recommend trimming (Anthropic caps CLAUDE.md at ~200 lines; OpenAI caps AGENTS.md at ~150).
  - A present root file with `mentions_commands == false` — recommend listing test/build/lint commands in a code-fenced block (Codex is trained to run commands referenced there; Claude and Gemini guidance is the same).
  - **Layering**: when `repo_profile` shows ≥4 visible top-level dirs and `agent_config.nested_instructions == []`, recommend a layered CLAUDE.md under the busiest subtrees so per-area conventions and scoped test/lint commands stop polluting the root file.
- **Agent config grading** (new pillar; reads `static.agent_config`). Measures whether the repo actively configures the agent runtime, not just documents intent. Grade by `config_score` (0–5 affordances: nested instructions, settings.json with deny rules, hooks, MCP servers, codebase map):
  - `A`: `config_score >= 3` *and* `instructions_age_days` is null or ≤ 180.
  - `ok` floor: `config_score >= 2`.
  - `warn`: `config_score == 1`, or `config_score >= 2` but `instructions_age_days > 365` (stale).
  - `bad`: `config_score == 0` on a non-trivial repo (`repo_profile.tracked_files > 50`).
  - `D`: `agent_instructions.mirror_parity == "missing-both"` (no root instructions → folds in here; the Instructions pillar already flags it, this caps the related runtime story).
  Recommendations should be specific: name the missing affordance (e.g. "add `.claude/settings.json` with `permissions.deny` for `dist/`, `node_modules/`, `build/` — the article calls these out as the noise dirs that bloat reads"). Suggest hooks (Stop hook for self-improving CLAUDE.md, SessionStart hook for dynamic context) only when the repo is large enough to benefit (`tracked_files > 200`). Suggest MCP servers only when the repo's README or docs reference an external service (heuristic: leave this to the orchestrator's judgement).
- **Tests coverage** (code shape, reads `static.tests` + `static.agent_config.nested_instructions`): cap at `warn` if `coverage_tool == null` OR `source_test_mapping.coverage_ratio < 0.7`; cap at `bad` if `coverage_ratio < 0.3`. `A` requires both a detected coverage tool AND `coverage_ratio ≥ 0.7`. Additionally cap at `warn` for monorepos (`repo_profile.heaviest_dirs` shows ≥3 distinct top-level dirs each with their own build manifest) whose root CLAUDE.md mentions commands but `agent_config.nested_with_commands == 0` — the article specifically warns that root-only test commands cause full-suite runs and timeouts. Surface a recommendation listing the top `uncovered_modules` and (when monorepo) the subdirs that need scoped command sections.
- **Harness-pillar grading**:
  - **Evals** (reads `static.evals`): `A` when `uncovered_items == []`, `quality_issues == []`, and `n_total_cases ≥ 5`. Cap at `warn` if any item is uncovered or any case file has quality issues (missing triggers, missing output assertions, or unresolved fixtures). `bad` if more than half of `coverage[]` is uncovered. `D` when `has_dir == false`. Surface a recommendation listing uncovered plugins/skills and the specific quality problems.
  - **Skill quality** (reads `static.skill_quality`): `A` when every skill has frontmatter + description and none exceed ~200 lines. Each missing description drops one notch; each over-limit skill caps the pillar at `warn`.
  - **Prompt hygiene** (reads `static.prompt_hygiene`): `A` when no markdown file exceeds 300 lines and `total_lines` is reasonable for the repo size. One oversized file → `warn`; ≥3 oversized → `bad`. Recommend splitting long guides into referenced subdocs.
- `warnings[]` is for degradations only you can see (e.g. `CLAUDE_CODE_SUBAGENT_MODEL` override, "feature-add blocked on permission"). `finalize` does not add any of its own anymore.

### 5. Reply

Read `report_path` and `warnings` from the `finalize` output and emit exactly the lines below. Status lines are emoji-prefixed, capitalized, and describe the activity generically — never name a specific guide id, provider, or model:

```
🎯  <grade> · <one-line rationale>
🧾  <absolute report_path>:1
⚠️  Warnings: <N> — <one-phrase summary>      ← omit this line when N == 0
👀  Pop the report open in your browser to review the full scorecard.
```

The closing "Pop the report open…" line is **mandatory** — it is the explicit hand-off telling the user the run is done and the HTML is what to look at next. Do not paraphrase it into a summary. The HTML scorecard surfaces every warning in full.

## Stop rules

- **`prepare` fails**: abort, quote stderr, write nothing.
- **`attribute` reports no readable session logs**: continue; pass the not-found sessions to `finalize` anyway. The renderer marks Cost `unavailable` and surfaces a warning. Add a recommendation to check `~/.claude/projects/` permissions.
- **Schema unrecognized for a session**: record it as-is and continue — one bad transcript must not crash the run.
- **No benchmark candidate** for a task (empty repo, no TODOs, no module to refactor): record `skipped: no candidate` in the matrix; never fabricate a target.
- **Subagent overrun**: terminate at budget; record `incomplete: budget exceeded` with whatever partial counters are available.
- **Subagent permission stall**: same as overrun — the unverified-ship contract means it should not happen, but if it does, terminate and record the cell.
- **`finalize` fails**: abort with the stderr quoted; the `tmp/` directory stays on disk for debugging.
