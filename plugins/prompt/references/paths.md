# Cache path conventions

The `draft`, `review`, and `revise` skills write their artifacts under a single static base:

```
$HOME/.claude/cache/prompt/<mode>/<project-slug>/<stamp>/
```

- `<mode>` — `draft`, `review`, or `revise`.
- `<project-slug>` — basename of the current working directory (e.g. `cc-plugins`).
- `<stamp>` — `YYYYMMDD-HHMMSS`, computed inline from today's date + current time. On the rare collision, append `-2`, `-3`, … so a prior run is never overwritten.

## Per-mode contents

**draft** — `…/draft/<project>/<stamp>/`
- `<slug>.md` — the working file the skill builds section by section.

**review** — `…/review/<project>/<stamp>/`
- `.<slug>.json` — payload written by the skill (leading dot keeps it out of casual `ls`).
- `<slug>.html` — rendered report (produced by `skills/review/scripts/render_report.py --project <project> --stamp <stamp> --slug <slug>`).

**revise** — `…/revise/<project>/<stamp>/`
- `<slug>-revised.md` — the working file the skill edits in place.

Skills compute the stamp themselves (from system date) and rely on `Write` to create the parent directory. No Bash call is needed to mint the run dir.
