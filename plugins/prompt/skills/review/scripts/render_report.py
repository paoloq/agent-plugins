#!/usr/bin/env python3
"""Render the prompt-review HTML report.

Resolves input/output paths from --project, --stamp, --slug under the static
base `$HOME/.claude/cache/prompt/review/`. Reads `.<slug>.json` and writes
`<slug>.html` next to it. See `plugins/prompt/references/paths.md` for the
canonical cache-path convention.

Input shape:

    {
      "prompt_title":   "short label",        # used in <title>
      "slug":           "kebab-case",         # informational
      "prompt_path":    "/abs/or/rel/path",   # optional; shown in header
      "prompt_excerpt": "first ~500 chars of the assessed prompt",
      "context": {                            # optional, from skill step 1.5
        "structured_output":   "yes|no|unknown",
        "runtime_user_input":  "yes|no|unknown",
        "tools_or_agentic":    "yes|no|unknown",
        "target_model_family": "openai|anthropic|google|mixed|unknown",
        "notes":               "one-line summary"
      },
      "guides": [
        {"provider":"openai|anthropic|google", "title":"...", "url":"..."}
      ],
      "interventions": [
        {"severity":   "Critical|High|Medium|Low|Info",
         "title":      "one-line headline",
         "rationale":  "why this matters for this prompt",
         "suggested":  "concrete rewrite / addition",          # optional
         "citations": [
           {"provider":"openai", "title":"...", "section":"§ ...", "url":"..."}
         ]}
      ]
    }
"""
from __future__ import annotations

import argparse
import datetime
import html
import json
import sys
from pathlib import Path

BASE_DIR = Path.home() / ".claude" / "cache" / "prompt" / "review"

SEVERITIES = ["Critical", "High", "Medium", "Low", "Info"]
SEV_TONE = {"Critical": "crit", "High": "high", "Medium": "med", "Low": "low", "Info": "info"}
SEV_ORDER = {s: i for i, s in enumerate(SEVERITIES)}

CONTEXT_LABELS = {
    "structured_output":   "Structured output",
    "runtime_user_input":  "Runtime user input",
    "tools_or_agentic":    "Tools / agentic",
    "target_model_family": "Target model",
}

_ASSETS = Path(__file__).resolve().parent / "assets"
CSS = (_ASSETS / "report.css").read_text(encoding="utf-8")
THEME_BOOT_JS = (_ASSETS / "theme-boot.js").read_text(encoding="utf-8")
JS = (_ASSETS / "report.js").read_text(encoding="utf-8")


def e(s) -> str:
    return html.escape("" if s is None else str(s), quote=True)


def count_by_severity(interventions: list[dict]) -> dict[str, int]:
    counts = {s: 0 for s in SEVERITIES}
    for item in interventions:
        sev = item.get("severity", "Info")
        counts[sev if sev in counts else "Info"] += 1
    return counts


def verdict_for(counts: dict[str, int]) -> tuple[str, str, str]:
    total = sum(counts.values())
    if total == 0:
        return "Ready to ship", "low", "No interventions surfaced against the official guides."
    if counts["Critical"] > 0:
        return ("Critical issues",
                "crit",
                f"{counts['Critical']} critical finding{'s' if counts['Critical'] != 1 else ''} "
                "could break the prompt's behavior — fix before shipping.")
    if counts["High"] > 0:
        return ("Needs work",
                "high",
                f"{counts['High']} high-severity issue{'s' if counts['High'] != 1 else ''} "
                "violate documented best practices.")
    if counts["Medium"] > 0:
        return ("Room to improve",
                "med",
                f"{counts['Medium']} medium suggestion{'s' if counts['Medium'] != 1 else ''} "
                "would tighten output quality.")
    if counts["Low"] > 0:
        return "Looks solid", "low", "Only minor refinements left."
    return "Looks solid", "info", "Just informational notes."


def render_distribution(counts: dict[str, int]) -> str:
    total = sum(counts.values()) or 1
    bar_segments: list[str] = []
    legend: list[str] = []
    summary_parts = [f"{counts[s]} {s.lower()}" for s in SEVERITIES if counts[s] > 0]
    summary = "Severity distribution: " + (", ".join(summary_parts) if summary_parts else "no interventions")
    for sev in SEVERITIES:
        n = counts[sev]
        tone = SEV_TONE[sev]
        if n > 0:
            pct = 100.0 * n / total
            bar_segments.append(
                f'<span class="{tone}" style="width:{pct:.2f}%" '
                f'aria-hidden="true" title="{e(sev)}: {n}"></span>'
            )
        disabled = "" if n > 0 else " disabled aria-disabled=\"true\""
        plural = "" if n == 1 else "s"
        legend.append(
            f'<button type="button" class="{tone}" data-sev="{e(sev)}"{disabled} '
            f'aria-pressed="false" aria-label="Filter to {e(sev)} severity ({n} item{plural})">'
            f'<span class="swatch" aria-hidden="true"></span><span>{e(sev)}</span>'
            f'<span class="count" aria-hidden="true">{n}</span></button>'
        )
    legend.append(
        '<button type="button" class="clear" style="display:none" '
        'aria-label="Clear severity filter">Clear filter</button>'
    )
    if sum(counts.values()) == 0:
        bar = '<span style="width:100%;background:var(--panel-2)" aria-hidden="true"></span>'
    else:
        bar = "".join(bar_segments)
    return (
        f'<div class="dist">'
        f'<div class="bar" role="img" aria-label="{e(summary)}">{bar}</div>'
        f'<div class="legend" role="group" aria-label="Filter interventions by severity">'
        f'{"".join(legend)}</div></div>'
    )


def render_hero(title: str, counts: dict[str, int]) -> str:
    verdict, tone, summary = verdict_for(counts)
    return (
        f'<div class="hero">'
        f'<p class="eyebrow">Prompt review</p>'
        f'<h1 id="report-title">{e(title)} <span class="verdict-tag {tone}" '
        f'role="status">{e(verdict)}</span></h1>'
        f'<p class="summary">{e(summary)}</p>'
        f'{render_distribution(counts)}'
        f'</div>'
    )


PROVIDER_LABEL = {"openai": "OpenAI", "anthropic": "Anthropic", "google": "Google"}


def render_guides_panel(guides: list[dict]) -> str:
    if not guides:
        return ""
    chips: list[str] = []
    for g in guides:
        provider = g.get("provider", "")
        label = PROVIDER_LABEL.get(provider, provider) or "source"
        tooltip = g.get("title", "") or label
        url = g.get("url", "")
        inner = f'<span class="dot" aria-hidden="true"></span>{e(label)}'
        chips.append(
            f'<a class="chip mute" href="{e(url)}" target="_blank" rel="noopener" '
            f'title="{e(tooltip)}">{inner}</a>'
            if url else
            f'<span class="chip mute" title="{e(tooltip)}">{inner}</span>'
        )
    return (
        f'<div class="panel"><h2>Guides</h2>'
        f'<div class="chiprow">{"".join(chips)}</div></div>'
    )


CONTEXT_FLAGS = [
    ("structured_output",  "JSON output", "{ }"),
    ("runtime_user_input", "User input",  "›_"),
    ("tools_or_agentic",   "Tools",       "⚙"),
]


def render_context_panel(context: dict) -> str:
    if not context:
        return ""
    chips: list[str] = []
    for key, label, glyph in CONTEXT_FLAGS:
        val = (context.get(key) or "").lower()
        if val not in ("yes", "no"):  # skip 'unknown' — uninformative
            continue
        tone = "on" if val == "yes" else "off"
        state = "on" if val == "yes" else "off"
        chips.append(
            f'<span class="chip ctx {tone}">'
            f'<span class="glyph" aria-hidden="true">{e(glyph)}</span>{e(label)}'
            f'<span class="sr-only"> ({state})</span></span>'
        )
    target = (context.get("target_model_family") or "").lower()
    if target and target not in ("unknown", "mixed"):
        target_label = PROVIDER_LABEL.get(target, target)
        chips.append(
            f'<span class="chip ctx target">'
            f'<span class="glyph" aria-hidden="true">◆</span>{e(target_label)}'
            f'<span class="sr-only"> (target model family)</span></span>'
        )
    if not chips:
        return ""
    return (
        f'<div class="panel"><h2>Context</h2>'
        f'<div class="chiprow">{"".join(chips)}</div></div>'
    )


def render_intervention(item: dict) -> str:
    sev = item.get("severity", "Info")
    if sev not in SEV_TONE:
        sev = "Info"
    tone = SEV_TONE[sev]
    title = item.get("title", "")
    rationale = item.get("rationale", "")
    suggested = item.get("suggested") or ""
    cites = item.get("citations") or []

    cite_html = ""
    if cites:
        rows = []
        for c in cites:
            url = c.get("url", "")
            label_parts = [c.get("title", ""), c.get("section", "")]
            label = " ".join(p for p in label_parts if p).strip() or url or "source"
            link = (
                f'<a href="{e(url)}" target="_blank" rel="noopener">{e(label)}</a>'
                if url else e(label)
            )
            rows.append(
                f'<div><span class="prov">{e(c.get("provider",""))}</span> {link}</div>'
            )
        cite_html = (
            f'<h4>Citations</h4><div class="cites">{"".join(rows)}</div>'
        )

    suggest_html = ""
    if suggested:
        suggest_html = (
            f'<h4>Suggested change</h4>'
            f'<div class="suggest">'
            f'<button class="copy" type="button" aria-label="Copy to clipboard" title="Copy to clipboard">'
            f'<svg class="copy-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
            f'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
            f'<rect x="9" y="9" width="11" height="11" rx="2"/>'
            f'<path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>'
            f'</svg>'
            f'<svg class="ok-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
            f'stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
            f'<polyline points="4 12 10 18 20 6"/>'
            f'</svg>'
            f'</button>'
            f'<span class="suggest-text">{e(suggested)}</span>'
            f'</div>'
        )

    return (
        f'<article class="iv {tone}" data-sev="{e(sev)}" aria-label="{e(sev)} intervention: {e(title)}">'
        f'<span class="rail" aria-hidden="true"></span>'
        f'<details>'
        f'<summary>'
        f'<span class="pill" aria-hidden="true">{e(sev)}</span>'
        f'<h3 class="iv-title">{e(title)}</h3>'
        f'<span class="caret" aria-hidden="true">▸</span>'
        f'</summary>'
        f'<div class="body">'
        f'<p>{e(rationale)}</p>'
        f'{suggest_html}{cite_html}'
        f'</div>'
        f'</details>'
        f'</article>'
    )


def render_interventions_section(interventions: list[dict]) -> str:
    if not interventions:
        return (
            '<section class="iv-section">'
            '<div class="iv-empty"><div class="big">✓</div>'
            '<div>No interventions — this prompt is clear of issues against the consulted guides.</div>'
            '</div></section>'
        )
    ordered = sorted(
        interventions,
        key=lambda i: (SEV_ORDER.get(i.get("severity", "Info"), 99), i.get("title", "")),
    )
    items = "".join(render_intervention(i) for i in ordered)
    return f'<section class="iv-section">{items}</section>'


def render_top(repo_path: str, ts: str, iso_ts: str) -> str:
    if repo_path:
        path_html = (
            f'<span class="path" title="{e(repo_path)}">'
            f'<span class="sr-only">Prompt path: </span>{e(repo_path)}</span>'
        )
    else:
        path_html = ""
    toggle = (
        '<button class="theme-toggle" type="button" id="theme-toggle" '
        'aria-label="Switch to light theme" title="Switch theme">'
        '<svg class="icon-moon" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>'
        '<svg class="icon-sun" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        '<circle cx="12" cy="12" r="4"/>'
        '<path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41'
        'M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41"/></svg>'
        '</button>'
    )
    return (
        f'<header class="top" role="banner">'
        f'<div class="brand"><span class="dot" aria-hidden="true"></span>'
        f'<span>prompt-review</span></div>'
        f'{path_html}'
        f'<time class="ts" datetime="{e(iso_ts)}">'
        f'<span class="sr-only">Generated </span>{e(ts)}</time>'
        f'{toggle}'
        f'</header>'
    )


def build_html(data: dict) -> str:
    title = data.get("prompt_title") or "untitled"
    prompt_path = data.get("prompt_path") or ""
    excerpt = data.get("prompt_excerpt") or ""
    guides = data.get("guides") or []
    interventions = data.get("interventions") or []
    context = data.get("context") or {}
    now = datetime.datetime.now()
    iso_ts = now.isoformat(timespec="seconds")
    day = str(now.day)
    hour = str(((now.hour - 1) % 12) + 1)
    ts = now.strftime(f"%b {day}, %Y · {hour}:%M %p")

    counts = count_by_severity(interventions)
    total = sum(counts.values())

    panels: list[str] = []
    guides_html = render_guides_panel(guides)
    context_html = render_context_panel(context)
    if guides_html:
        panels.append(guides_html)
    if context_html:
        panels.append(context_html)
    panels_html = f'<div class="panels">{"".join(panels)}</div>' if panels else ""

    excerpt_html = ""
    if excerpt:
        excerpt_html = (
            f'<details class="excerpt"><summary>Prompt excerpt '
            f'<span class="muted" style="font-size:12px">({len(excerpt)} chars)</span></summary>'
            f'<pre>{e(excerpt)}</pre></details>'
        )

    iv_label = f'Interventions · {total} total' if total else 'Interventions'
    interventions_header = (
        f'<section class="h-section" id="interventions" aria-labelledby="iv-heading">'
        f'<h2 class="h-title" id="iv-heading">{iv_label}</h2></section>'
    )

    return (
        "<!doctype html>\n<html lang=\"en\"><head>"
        "<meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<title>Prompt review · {e(title)}</title>"
        f"<script>{THEME_BOOT_JS}</script>"
        f"<style>{CSS}</style></head><body>"
        f'<a class="skip-link" href="#interventions">Skip to interventions</a>'
        f'<div role="status" aria-live="polite" class="sr-only" id="live-region"></div>'
        f'<main aria-labelledby="report-title">'
        f'{render_top(prompt_path, ts, iso_ts)}'
        f'{render_hero(title, counts)}'
        f'{panels_html}'
        f'{interventions_header}'
        f'{render_interventions_section(interventions)}'
        f'{excerpt_html}'
        f'</main><script>{JS}</script></body></html>\n'
    )


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True, help="project slug")
    ap.add_argument("--stamp", required=True, help="run stamp (e.g. YYYYMMDD-HHMMSS)")
    ap.add_argument("--slug", required=True, help="prompt slug")
    args = ap.parse_args(argv)

    run_dir = BASE_DIR / args.project / args.stamp
    in_path = run_dir / f".{args.slug}.json"
    out_path = run_dir / f"{args.slug}.html"

    data = json.loads(in_path.read_text(encoding="utf-8"))
    out_path.write_text(build_html(data), encoding="utf-8")
    print(out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
