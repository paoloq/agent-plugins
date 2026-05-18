# task

Plan a coding task end-to-end: draft a spec, deliberate the implementation plan with Codex, then hand off to implementation via `ExitPlanMode`.

## Skills

- **draft-spec** — Interview-driven SPEC drafter. Writes `./.specs/<slug>.md` against a fixed 5-section template (Objective, Context, Constraints, Success criteria & verification, Stop rules), asking only where recon leaves load-bearing ambiguity. Trigger: "draft spec", "write a spec", `/draft-spec`.
- **plan-council** — Multi-AI deliberation where Claude, Codex, and the user converge on an implementation plan. Plan mode is active throughout; the user has veto power. Trigger: "council", "deliberate", "plan with codex", "second opinion on the plan".

## Permissions

Grants write access to `./.specs/**` and enables `showClearContextOnPlanAccept`.
