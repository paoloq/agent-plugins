# task-spec

Draft a coding-task spec interactively, then hand it off to implementation in the same session via `ExitPlanMode`.

## Skills

- **draft** — Interview-driven SPEC drafter. Writes `./.specs/<slug>.md` against a fixed 5-section template (Objective, Context, Constraints, Success criteria & verification, Stop rules), asking only where recon leaves load-bearing ambiguity. Trigger: "draft spec", "write a spec", `/draft`.

## Permissions

Grants write access to `./.specs/**` and enables `showClearContextOnPlanAccept`.
