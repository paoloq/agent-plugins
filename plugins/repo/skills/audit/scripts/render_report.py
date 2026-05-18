#!/usr/bin/env python3
"""Render the agentic-readiness HTML report from three JSON inputs.

Inputs (all required):
    --static    JSON emitted by static_checks.py
    --usage     JSON emitted by session_usage.py (keyed by session id)
    --matrix    JSON authored by the orchestrating agent — grade, rationale,
                per-dimension scores, per-task benchmark cells, recommendations

Output:
    --out       path to write the self-contained HTML (default: ./.agentic-readiness.html)

The matrix JSON shape (single-model benchmark — by default subagents inherit
the orchestrator's model. The CLAUDE_CODE_SUBAGENT_MODEL env var, if set,
overrides the per-invocation `model:` parameter and the subagent frontmatter,
so the actual model can differ from the orchestrator's. The report's "what
would this cost on X?" rows are projections, not real runs):

    {
      "repo_path":          "/abs/path",
      "surface":            "claude" | "codex",
      "orchestrator_model": "claude-opus-4-7",     // the orchestrator session's model (optional)
      "actual_model":       "claude-sonnet-4-6",   // the model subagents actually ran on (read from session logs)
      "grade":            "A" | "B" | "C" | "D",
      "rationale":        "one-line summary",
      "scores": [{"label": "...", "grade": "B", "tone": "ok|warn|bad"}, ...],
      "benchmark": [
        {
          "task":         "Repo walk",
          "session_id":   "abc-123",     // joins to --usage
          "wall_clock_s": 42.1,
          "outcome":      "completed" | "incomplete: budget exceeded" | "skipped: no candidate"
        },
        ...
      ],
      "recommendations": ["...", "..."],
      "warnings":        ["..."]   // optional; auto-warnings added by renderer
    }
"""
from __future__ import annotations

import argparse
import datetime
import html
import json
import sys
from pathlib import Path

# Per-million-token list rates. Sourced from upstream pricing pages (May 2026).
# Anthropic cache_write is the 5-minute TTL rate (1.25x input); 1h TTL is 2x.
# Refs:
#   https://platform.claude.com/docs/en/about-claude/pricing
#   https://developers.openai.com/api/docs/pricing
LOAD_RATES_PER_M_INPUT: dict[str, float] = {
    "claude-opus-4-7":   5.0,
    "claude-sonnet-4-6": 3.0,
    "claude-haiku-4-5":  1.0,
    "gpt-5.5":           5.0,
    "gpt-5.4":           2.5,
}
PROJECTION_RATES: dict[str, dict[str, float]] = {
    "claude-opus-4-7":   {"input":  5.0, "output": 25.0, "cache_read": 0.50, "cache_write": 6.25},
    "claude-sonnet-4-6": {"input":  3.0, "output": 15.0, "cache_read": 0.30, "cache_write": 3.75},
    "claude-haiku-4-5":  {"input":  1.0, "output":  5.0, "cache_read": 0.10, "cache_write": 1.25},
    "gpt-5.5":           {"input":  5.0, "output": 30.0, "cache_read": 0.50, "cache_write": 0.0},
    "gpt-5.4":           {"input":  2.5, "output": 15.0, "cache_read": 0.25, "cache_write": 0.0},
}


def project_usd(u: dict, model_id: str) -> float | None:
    rate = PROJECTION_RATES.get(model_id)
    if rate is None:
        return None
    per_m = 1_000_000.0
    return round(
        int(u.get("input_tokens", 0) or 0)                * rate["input"]       / per_m
        + int(u.get("output_tokens", 0) or 0)             * rate["output"]      / per_m
        + int(u.get("cache_read_input_tokens", 0) or 0)   * rate["cache_read"]  / per_m
        + int(u.get("cache_creation_input_tokens", 0) or 0) * rate["cache_write"] / per_m,
        4,
    )


def project_cold_usd(u: dict, model_id: str) -> float | None:
    """Project USD assuming no cache benefit — every input-side bucket is
    billed at the list input rate. Models the first run, or the first run
    after the prompt cache TTL has expired."""
    rate = PROJECTION_RATES.get(model_id)
    if rate is None:
        return None
    per_m = 1_000_000.0
    cold_input = (
        int(u.get("input_tokens", 0) or 0)
        + int(u.get("cache_read_input_tokens", 0) or 0)
        + int(u.get("cache_creation_input_tokens", 0) or 0)
    )
    return round(
        cold_input * rate["input"] / per_m
        + int(u.get("output_tokens", 0) or 0) * rate["output"] / per_m,
        4,
    )

CSS = """
:root {
  /* Dark theme defaults. -fg variants are tuned for text contrast against
     --panel (#151926). Raw color variants are used for fills (bars, dots,
     gradient stripes) where only the 3:1 UI-component rule applies. */
  --bg:#0b0d12; --panel:#151926; --panel-2:#1c2133;
  --ink:#f1f4fa;            /* ~18:1 on --bg, AAA */
  --mute:#a4adc1;           /* ~7.5:1 on --panel, AAA */
  --line:#2a3045; --line-2:#3a4260;
  --accent:#9ebaff;         /* ~9:1 on --panel, AAA */
  --accent-ink:#0b1020;
  --accent-bg:rgba(158,186,255,.16);

  --ok:#5cd2a8;   --ok-fg:#74dab4;    --ok-bg:rgba(92,210,168,.16);
  --warn:#f5c451; --warn-fg:#f5c451;  --warn-bg:rgba(245,196,81,.18);
  --bad:#ff5d6c;  --bad-fg:#ff8a93;   --bad-bg:rgba(255,93,108,.16);

  --radius:14px; --radius-sm:10px; --radius-xs:6px;
  --shadow:0 1px 0 rgba(255,255,255,.03) inset, 0 6px 24px rgba(0,0,0,.25);
  --bg-glow-a:color-mix(in srgb, var(--accent) 8%, transparent);
  --bg-glow-b:color-mix(in srgb, #8b5cf6      6%, transparent);
}
:root[data-theme="light"] {
  --bg:#f7f9fd; --panel:#ffffff; --panel-2:#eef1f8;
  --ink:#0e1422;            /* ~18:1 on white, AAA */
  --mute:#4a5365;           /* ~8.0:1 on --bg, AAA */
  --line:#d4dae8; --line-2:#a9b2c5;
  --accent:#1d4ed8;         /* ~7.7:1 on white, AAA */
  --accent-ink:#ffffff;
  --accent-bg:rgba(29,78,216,.14);

  --ok:#047857;   --ok-fg:#065f46;    --ok-bg:rgba(16,185,129,.20);   /* fg ~7.5:1 */
  --warn:#b45309; --warn-fg:#92400e;  --warn-bg:rgba(217,119,6,.20);  /* fg ~7.1:1 */
  --bad:#dc2626;  --bad-fg:#b91c1c;   --bad-bg:rgba(220,38,38,.16);   /* fg ~7.5:1 */

  --shadow:0 1px 0 rgba(255,255,255,.9) inset, 0 1px 2px rgba(15,23,42,.04),
           0 12px 32px rgba(15,23,42,.10);
  --bg-glow-a:color-mix(in srgb, var(--accent) 8%, transparent);
  --bg-glow-b:color-mix(in srgb, #8b5cf6      5%, transparent);
}
@media (forced-colors: active) {
  :root { --shadow:none }
  .chip, .hero, .kpi, .dim, table, ol.recs li, details.more, nav.side,
  .theme-toggle, .warnings { border-color:CanvasText !important }
  .dim .bar > i, .ibar .fill, .hero::before { forced-color-adjust:none }
}
* { box-sizing:border-box }
:focus { outline:none }
:focus-visible { outline:2px solid var(--accent); outline-offset:2px; border-radius:6px }
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation:none !important; transition:none !important }
}
.sr-only { position:absolute; width:1px; height:1px; padding:0; margin:-1px;
  overflow:hidden; clip:rect(0,0,0,0); white-space:nowrap; border:0 }
.skip-link { position:absolute; left:8px; top:-40px; background:var(--accent);
  color:var(--accent-ink); padding:8px 14px; border-radius:6px; font-weight:600;
  z-index:100; transition:top .15s ease }
.skip-link:focus { top:8px }
html,body { margin:0; background:
  radial-gradient(1200px 600px at 100% -10%, var(--bg-glow-a), transparent 60%),
  radial-gradient(900px 500px at -10% 110%, var(--bg-glow-b), transparent 55%),
  var(--bg);
  color:var(--ink);
  font:14.5px/1.55 ui-sans-serif,system-ui,-apple-system,Segoe UI,Inter,sans-serif;
  -webkit-font-smoothing:antialiased; }
a { color:var(--accent); text-decoration:none } a:hover { text-decoration:underline }
button { font:inherit; color:inherit }

.app { display:grid; grid-template-columns:248px 1fr; min-height:100vh }
nav.side { position:sticky; top:0; height:100vh; padding:22px 14px;
  border-right:1px solid var(--line); background:var(--panel); overflow:auto;
  box-shadow:var(--shadow) }
nav.side .brand { display:flex; align-items:center; gap:8px; padding:4px 6px 16px;
  font-weight:600; letter-spacing:.01em }
nav.side .brand .dot { width:10px; height:10px; border-radius:3px;
  background:linear-gradient(135deg,var(--accent),#a47bff) }
nav.side h1 { font-size:11px; letter-spacing:.1em; text-transform:uppercase;
  color:var(--mute); margin:14px 6px 6px; font-weight:600 }
nav.side ol { list-style:none; padding:0; margin:0; counter-reset:step }
nav.side li { counter-increment:step; margin:1px 0 }
nav.side a { display:flex; align-items:center; gap:10px; padding:7px 10px;
  border-radius:var(--radius-sm); color:var(--ink); font-size:13.5px;
  transition:background .15s ease }
nav.side a::before { content:counter(step); display:inline-flex;
  width:18px; height:18px; align-items:center; justify-content:center;
  border-radius:4px; background:var(--panel-2); color:var(--mute);
  font:11px/1 ui-monospace,SF Mono,Menlo,monospace; font-variant-numeric:tabular-nums }
nav.side a:hover { background:var(--panel-2); text-decoration:none }
nav.side a.active { background:var(--accent-bg); color:var(--accent) }
nav.side a.active::before { background:var(--accent); color:var(--accent-ink) }

main { padding:0 40px 80px; max-width:1080px; width:100% }

header.top { position:sticky; top:0; z-index:10; padding:18px 0 14px;
  background:linear-gradient(var(--bg) 70%, transparent);
  display:flex; align-items:center; gap:14px; flex-wrap:wrap }
header.top .repo { font-size:15px; font-weight:600 }
header.top .path { color:var(--mute); font:12.5px/1.5 ui-monospace,SF Mono,Menlo,monospace;
  background:var(--panel-2); padding:3px 8px; border-radius:6px;
  word-break:break-all }
header.top .ts { color:var(--mute); font-size:12.5px; margin-left:auto }
header.top button.copy { font:inherit; font-size:12px; color:var(--mute);
  background:var(--panel); border:1px solid var(--line); border-radius:6px;
  padding:5px 10px; cursor:pointer;
  transition:color .15s ease, border-color .15s ease }
header.top button.copy:hover { color:var(--ink); border-color:var(--line-2) }
.theme-toggle { background:var(--panel); border:1px solid var(--line); color:var(--mute);
  width:36px; height:36px; min-width:36px; display:inline-flex; align-items:center;
  justify-content:center; border-radius:999px; cursor:pointer; padding:0;
  transition:color .15s ease, border-color .15s ease, background .15s ease }
.theme-toggle:hover { color:var(--ink); border-color:var(--line-2) }
.theme-toggle svg { width:16px; height:16px; display:block }
.theme-toggle .icon-sun  { display:none }
.theme-toggle .icon-moon { display:block }
:root[data-theme="light"] .theme-toggle .icon-sun  { display:block }
:root[data-theme="light"] .theme-toggle .icon-moon { display:none }

section { scroll-margin-top:24px; padding:28px 0 4px;
  border-top:1px solid var(--line) }
section:first-of-type { border-top:0; padding-top:8px }
h2 { margin:0 0 18px; font-size:20px; letter-spacing:-.01em }
h3 { margin:22px 0 10px; font-size:12px; color:var(--mute); font-weight:600;
  letter-spacing:.08em; text-transform:uppercase }

/* ---- Hero scorecard ---- */
.hero { display:grid; grid-template-columns:auto 1fr; gap:22px; align-items:start;
  padding:22px; background:var(--panel); border:1px solid var(--line);
  border-radius:var(--radius); box-shadow:var(--shadow); margin-bottom:18px;
  position:relative; overflow:hidden }
.hero::before { content:''; position:absolute; inset:0 0 auto 0; height:3px;
  background:linear-gradient(90deg, var(--bad), var(--warn), var(--ok), var(--accent)) }
.hero .grade { width:88px; height:88px; border-radius:18px; display:grid;
  place-items:center; font-size:44px; font-weight:700; letter-spacing:-.02em;
  background:var(--accent-bg); color:var(--accent);
  border:1px solid color-mix(in srgb, var(--accent) 30%, transparent) }
.hero.tone-ok   .grade { background:var(--ok-bg);   color:var(--ok-fg);
  border-color:color-mix(in srgb, var(--ok) 30%, transparent) }
.hero.tone-warn .grade { background:var(--warn-bg); color:var(--warn-fg);
  border-color:color-mix(in srgb, var(--warn) 30%, transparent) }
.hero.tone-bad  .grade { background:var(--bad-bg);  color:var(--bad-fg);
  border-color:color-mix(in srgb, var(--bad) 30%, transparent) }
.hero .rationale { margin:6px 0 14px; font-size:17px; line-height:1.45 }
.kpis { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
  gap:10px; margin-top:6px }
.kpi { padding:12px 14px; background:var(--panel-2); border-radius:var(--radius-sm);
  border:1px solid var(--line) }
.kpi .label { color:var(--mute); font-size:11.5px; text-transform:uppercase;
  letter-spacing:.06em; margin-bottom:4px }
.kpi .value { font:600 18px/1.2 ui-sans-serif,system-ui,Inter,sans-serif;
  font-variant-numeric:tabular-nums; letter-spacing:-.01em }
.kpi .sub { color:var(--mute); font-size:12px; margin-top:2px }

/* ---- Dimension cards ---- */
.dims { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr));
  gap:10px; margin-top:6px }
.dim { padding:12px 14px; background:var(--panel); border:1px solid var(--line);
  border-radius:var(--radius-sm); display:flex; flex-direction:column; gap:8px }
.dim .row { display:flex; align-items:baseline; justify-content:space-between; gap:8px }
.dim .label { font-weight:600; font-size:13.5px }
.dim .badge { font:600 13px/1 ui-monospace,SF Mono,Menlo,monospace;
  padding:3px 8px; border-radius:6px; background:var(--panel-2); color:var(--mute) }
.dim.tone-ok   .badge { background:var(--ok-bg);   color:var(--ok-fg) }
.dim.tone-warn .badge { background:var(--warn-bg); color:var(--warn-fg) }
.dim.tone-bad  .badge { background:var(--bad-bg);  color:var(--bad-fg) }
.dim .bar { height:4px; border-radius:3px; background:var(--panel-2); overflow:hidden }
.dim .bar > i { display:block; height:100%; border-radius:3px; background:var(--mute) }
.dim.tone-ok   .bar > i { background:var(--ok) }
.dim.tone-warn .bar > i { background:var(--warn) }
.dim.tone-bad  .bar > i { background:var(--bad) }

/* ---- Chips ---- */
.chiprow { display:flex; flex-wrap:wrap; gap:6px; margin:4px 0 }
.chip { display:inline-flex; align-items:center; gap:6px; padding:3px 10px;
  border-radius:999px; font-size:12px; background:var(--panel-2); color:var(--mute);
  border:1px solid var(--line) }
.chip.ok   { color:var(--ok-fg);   background:var(--ok-bg);   border-color:transparent }
.chip.warn { color:var(--warn-fg); background:var(--warn-bg); border-color:transparent }
.chip.bad  { color:var(--bad-fg);  background:var(--bad-bg);  border-color:transparent }

/* ---- Tables ---- */
table { width:100%; border-collapse:separate; border-spacing:0;
  font-variant-numeric:tabular-nums; margin:6px 0 8px;
  background:var(--panel); border:1px solid var(--line);
  border-radius:var(--radius-sm); overflow:hidden }
th,td { text-align:left; padding:9px 12px; border-bottom:1px solid var(--line) }
tbody tr:last-child td { border-bottom:0 }
tbody tr:hover { background:var(--panel-2) }
th { color:var(--mute); font-weight:600; font-size:11px; letter-spacing:.06em;
  text-transform:uppercase; background:var(--panel-2) }
th[title], .kpi .label[title], .dim .label[title], .chip[title] { cursor:help;
  text-decoration:underline dotted color-mix(in srgb,var(--mute) 60%,transparent);
  text-underline-offset:3px }
td.num, th.num { text-align:right; font-variant-numeric:tabular-nums }
td.bar-cell { width:34%; padding-right:14px }

/* ---- Inline bars ---- */
.ibar { display:flex; align-items:center; gap:8px }
.ibar .track { flex:1; height:8px; background:var(--panel-2); border-radius:5px;
  overflow:hidden; min-width:60px }
.ibar .fill { height:100%; border-radius:5px; background:var(--accent) }
.ibar .fill.ok   { background:var(--ok) }
.ibar .fill.warn { background:var(--warn) }
.ibar .fill.bad  { background:var(--bad) }
.ibar .fill.mute { background:var(--mute); opacity:.6 }
.ibar .val { color:var(--mute); font-size:12px; min-width:64px; text-align:right }
.ibar.highlight .track { box-shadow:0 0 0 2px color-mix(in srgb,var(--ok) 40%,transparent) }

.kbd { font:12px ui-monospace,SF Mono,Menlo,monospace;
  background:var(--panel-2); padding:1px 6px; border-radius:4px; color:var(--ink) }
.muted { color:var(--mute) }
ol.recs { padding-left:0; margin:0; list-style:none; counter-reset:rec }
ol.recs li { counter-increment:rec; position:relative; padding:10px 14px 10px 42px;
  background:var(--panel); border:1px solid var(--line);
  border-radius:var(--radius-sm); margin:6px 0 }
ol.recs li::before { content:counter(rec); position:absolute; left:12px; top:10px;
  width:22px; height:22px; border-radius:6px; background:var(--accent-bg);
  color:var(--accent); display:grid; place-items:center; font:600 12px/1 ui-monospace,monospace }

.warnings { margin-top:14px; padding:14px 16px; border-radius:var(--radius-sm);
  background:var(--bad-bg); border:1px solid color-mix(in srgb,var(--bad) 35%,transparent) }
.warnings .warn-title { color:var(--bad-fg); font-weight:600; margin-bottom:4px; font-size:13px }
.warnings ul { margin:4px 0 0 18px; padding:0 } .warnings li { margin:2px 0 }

details.more { margin-top:8px }
details.more > summary { cursor:pointer; color:var(--mute); font-size:12.5px;
  padding:6px 0; list-style:none }
details.more > summary::-webkit-details-marker { display:none }
details.more > summary::before { content:'▸ '; display:inline-block;
  transition:transform .15s; color:var(--mute) }
details.more[open] > summary::before { transform:rotate(90deg) translateX(-2px) }

@media (max-width:880px) {
  .app { grid-template-columns:1fr }
  nav.side { position:static; height:auto; border-right:0;
    border-bottom:1px solid var(--line) }
  nav.side ol { display:flex; flex-wrap:wrap; gap:4px }
  nav.side li { margin:0 }
  main { padding:0 18px 60px }
  .hero { grid-template-columns:1fr }
}

@media print {
  .app { grid-template-columns:1fr }
  nav.side, header.top button { display:none }
  main { padding:0; max-width:none }
  section { break-inside:avoid; border-top:1px solid #ccc }
  body { background:#fff; color:#000 }
  .hero, table, ol.recs li, .dim, .kpi { box-shadow:none; border-color:#ccc }
}
"""

THEME_BOOT_JS = """
(function () {
  try {
    var saved = localStorage.getItem('repo-audit-theme');
    var theme = (saved === 'light' || saved === 'dark')
      ? saved
      : (matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark');
    document.documentElement.setAttribute('data-theme', theme);
  } catch (_) {
    document.documentElement.setAttribute('data-theme', 'dark');
  }
})();
"""

SCROLLSPY = """
(function () {
  const root = document.documentElement;
  const toggle = document.getElementById('theme-toggle');
  const live = document.getElementById('live-region');

  function announce(msg) {
    if (live) { live.textContent = ''; setTimeout(() => { live.textContent = msg; }, 30); }
  }
  function syncToggleLabel() {
    if (!toggle) return;
    const cur = root.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
    const next = cur === 'light' ? 'dark' : 'light';
    toggle.setAttribute('aria-label', 'Switch to ' + next + ' theme');
    toggle.setAttribute('title', 'Switch to ' + next + ' theme');
    toggle.setAttribute('aria-pressed', cur === 'light' ? 'true' : 'false');
  }
  syncToggleLabel();

  if (toggle) {
    toggle.addEventListener('click', () => {
      const cur = root.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
      const next = cur === 'light' ? 'dark' : 'light';
      root.setAttribute('data-theme', next);
      try { localStorage.setItem('repo-audit-theme', next); } catch (_) {}
      syncToggleLabel();
      announce(next === 'light' ? 'Light theme on' : 'Dark theme on');
    });
  }
  try {
    const mq = matchMedia('(prefers-color-scheme: light)');
    mq.addEventListener('change', (ev) => {
      if (localStorage.getItem('repo-audit-theme')) return;
      root.setAttribute('data-theme', ev.matches ? 'light' : 'dark');
      syncToggleLabel();
    });
  } catch (_) {}

  const copyBtn = document.querySelector('header.top button.copy');
  if (copyBtn) copyBtn.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(copyBtn.dataset.path);
      const old = copyBtn.textContent;
      copyBtn.textContent = 'copied';
      announce('Path copied to clipboard');
      setTimeout(() => { copyBtn.textContent = old; }, 1200);
    } catch (_) { announce('Copy failed — clipboard unavailable'); }
  });
})();

const links = [...document.querySelectorAll('nav.side a')];
const sections = links.map(a => document.querySelector(a.getAttribute('href')));
function setActive(i) { links.forEach((a, j) => a.classList.toggle('active', j === i)); }
function onScroll() {
  // Bottom-of-page: always activate the last link.
  if (window.innerHeight + window.scrollY >= document.body.scrollHeight - 4) {
    setActive(sections.length - 1); return;
  }
  // Otherwise: last section whose top crossed 30% of viewport.
  const probe = window.scrollY + window.innerHeight * 0.3;
  let idx = 0;
  sections.forEach((s, i) => { if (s && s.offsetTop <= probe) idx = i; });
  setActive(idx);
}
window.addEventListener('scroll', onScroll, { passive: true });
window.addEventListener('resize', onScroll);
onScroll();
"""


# ---- formatting helpers --------------------------------------------------

def e(s) -> str:
    return html.escape("" if s is None else str(s), quote=True)


def fmt_int(n: float | int | None) -> str:
    if n is None:
        return "—"
    return f"{int(n):,}"


def fmt_usd(v: float | None) -> str:
    if v is None:
        return "—"
    return f"${v:,.4f}" if v < 1 else f"${v:,.2f}"


def fmt_secs(v: float | None) -> str:
    if v is None:
        return "—"
    if v < 1:
        return f"{int(v * 1000)} ms"
    if v < 60:
        return f"{v:.1f} s"
    m, s = divmod(int(v), 60)
    return f"{m} min {s:02d} s" if m < 60 else f"{m // 60} h {m % 60} min"


def fmt_bytes(n: float | int | None) -> str:
    if n is None:
        return "—"
    size = float(n)
    if size < 1024:
        return f"{int(size)} B"
    for unit in ("KB", "MB", "GB"):
        size /= 1024.0
        if size < 1024 or unit == "GB":
            return f"{size:,.1f} {unit}" if size < 10 else f"{size:,.0f} {unit}"
    return f"{size:,.0f} GB"


def fmt_tokens(n: float | int | None) -> str:
    if n is None:
        return "—"
    v = int(n)
    if v < 1_000:
        return f"{v} tokens"
    if v < 1_000_000:
        return f"{v / 1_000:.1f}K tokens" if v < 10_000 else f"{v // 1_000:,}K tokens"
    return f"{v / 1_000_000:.1f}M tokens"


def fmt_count(n: float | int | None, singular: str, plural: str | None = None) -> str:
    if n is None:
        return "—"
    v = int(n)
    word = singular if v == 1 else (plural or singular + "s")
    return f"{v:,} {word}"


def fmt_human_datetime(now: datetime.datetime) -> str:
    """e.g. 'May 15, 2026 · 6:32 PM'. Avoids %-d / %-I (BSD/GNU-only)."""
    day = str(now.day)
    hour = str(((now.hour - 1) % 12) + 1)
    return now.strftime(f"%b {day}, %Y · {hour}:%M %p")


MODEL_LABELS: dict[str, str] = {
    "claude-opus-4-7":    "Claude Opus 4.7",
    "claude-sonnet-4-6":  "Claude Sonnet 4.6",
    "claude-haiku-4-5":   "Claude Haiku 4.5",
    "gpt-5.5":            "GPT-5.5",
    "gpt-5.4":            "GPT-5.4",
    "gpt-5.3-codex":      "GPT-5.3 Codex",
    "gpt-5.2":            "GPT-5.2",
    "gpt-5.1":            "GPT-5.1",
    "gpt-5":              "GPT-5",
    "gemini-3":           "Gemini 3",
    "gemini-3.1":         "Gemini 3.1",
}


def friendly_model(model_id: str | None) -> str:
    if not model_id:
        return "unknown model"
    return MODEL_LABELS.get(model_id, model_id)


MIRROR_PARITY_LABEL: dict[str, str] = {
    "in-sync":      "Both files in sync",
    "claude-only":  "Only CLAUDE.md present",
    "agents-only":  "Only AGENTS.md present",
    "drift":        "Files differ",
    "missing-both": "No instruction files found",
    "n/a":          "Not applicable",
}


GLOSSARY: dict[str, str] = {
    # KPI labels
    "Run cost":         "Total USD billed across the five benchmark subagents in this run, from session logs.",
    "Cheapest model":   "Lowest projected USD if the same benchmark token profile were billed at another model's list rates.",
    "Wall time":        "Sum of subagent wall-clock times across all benchmark tasks.",
    "Repo size":        "Heuristic estimate of repo content size in tokens (~4 chars per token).",
    # Scorecard pillars
    "Repo profile":     "Tracked files, text files, and heaviest paths/dirs by estimated tokens.",
    "Instructions":     "Presence and quality of CLAUDE.md / AGENTS.md (line limits, commands, gotchas, mirror parity).",
    "Agent config":     "Runtime affordances the repo configures for agents: nested CLAUDE.md layering, .claude/settings.json deny rules, hooks, MCP servers, a codebase map, and instruction-file freshness.",
    "Tests":            "Test runners, linters, typecheckers, CI configs, coverage tool, and source-to-test mapping.",
    "Hygiene":          ".gitignore presence, secret-pattern hits in tracked files, and oversized binaries.",
    "Dev environment":  "Signals of a reproducible dev setup (lockfiles, devcontainer, language version pins, etc.).",
    "Observability":    "Signals of logging, tracing, metrics, or error reporting wiring.",
    "Security":         "Signals of security/governance scaffolding (CODEOWNERS, SECURITY.md, dependabot, SAST configs).",
    "Benchmark":        "Per-task subagent dispatch: outcome, tokens, USD, and wall time on the actual dispatch model.",
    "Cost":             "Actual subagent USD plus projections at each principal model's list rate (warm and cold).",
    "Evals":            "Presence of evals/, case files, total cases, item coverage, and case quality issues.",
    "Skill quality":    "Per-skill SKILL.md presence, frontmatter, description, and line count vs. the ~200-line cap.",
    "Prompt hygiene":   "Markdown file count, total lines, and any prompts over the 300-line readability threshold.",
    # Table headers
    "Path":             "Relative path from the repo root.",
    "Bytes":            "On-disk size of the file.",
    "Est. tokens":      "Heuristic token estimate (~4 chars per token); not an exact tokenizer count.",
    "Dir":              "Top-level directory under the repo root.",
    "Item":             "Plugin or skill the eval case file targets.",
    "Cases":            "Number of test cases declared in this eval file.",
    "Triggers":         "Whether the eval file declares trigger phrases that activate the item under test.",
    "+/-":              "Positive / negative case counts (cases expected to pass vs. fail).",
    "Output":           "Number of assertions on the model's output for this eval.",
    "Missing fixtures": "Fixtures referenced by cases but not found on disk.",
    "Frontmatter":      "Whether SKILL.md starts with valid YAML frontmatter.",
    "Description":      "Whether the skill's frontmatter description field is present.",
    "Lines":            "Line count of the file.",
    "Outcome":          "Whether the subagent completed, was skipped, or hit its budget.",
    "Task":             "Benchmark task name (Repo walk, Locate, Trace, Spot, Bug fix, Feature add, Refactor, Write a test).",
    "Walkability":      "How easily an agent can map this repo. Combines static signals (root dir count, duplicate-named dirs, generated dirs, prefix collisions) with the four description-shaped benchmark tasks (Repo walk, Locate, Trace, Spot) scored on specificity.",
    "Specificity":      "Orchestrator's judgement of a description-task response: high = concrete + correct count; medium = partial / hedged; low = vague.",
    "In":               "Input tokens billed for this subagent session.",
    "Out":              "Output tokens billed for this subagent session.",
    "Cache read":       "Tokens served from the prompt cache (5-minute TTL) at the cache-read discount.",
    "USD":              "Total USD billed for this subagent session, model-reported.",
    "Wall":             "Wall-clock seconds for this subagent dispatch.",
    "Model":            "Model id whose list rates are applied to project cost.",
    "Projected USD (warm)": "Cost at this model's rates assuming the same cache-read pattern as the actual run.",
    "USD (cold)":       "Cost at this model's input rate with no cache hits — first run, or after the 5-minute TTL expires.",
    "Kind":             "Type of secret pattern matched.",
    "Plugin / skill":   "Item under coverage (plugin or skill).",
    "Eval present":     "Whether at least one eval case targets this item.",
    "Problems":         "Quality issues detected in the eval case file.",
    # Chips
    "in-sync":          "Both CLAUDE.md and AGENTS.md exist and content matches.",
    "drift":            "Both files exist but their content has diverged.",
    "claude-only":      "Only CLAUDE.md present — AGENTS.md is absent.",
    "agents-only":      "Only AGENTS.md present — CLAUDE.md is absent.",
    "missing-both":     "Neither CLAUDE.md nor AGENTS.md was found.",
    "completed":        "Subagent finished and produced its final message.",
    "incomplete":       "Subagent exceeded its wall-clock or permission budget.",
    "skipped":          "No suitable target for this task (e.g. no test directory).",
    "actual run":       "The model the benchmark actually dispatched on; other rows are projections.",
    "cheapest":         "Lowest projected cost across the principal models for this token profile.",
    # Section headers (h2) not already covered above
    "Scorecard":        "Overall grade, pillar dimensions, and headline KPIs for this run.",
    "Recommendations":  "Top suggestions derived from the lowest-scoring pillars and detected gaps.",
    # Subsection headers (h3)
    "Heaviest paths":   "Largest individual files in the repo by estimated tokens.",
    "Heaviest top-level dirs": "Top-level directories ranked by estimated token weight.",
    "Runners":          "Detected test runners (e.g. pytest, jest, go test).",
    "Linters":          "Detected linters (e.g. ruff, eslint, golangci-lint).",
    "Typecheckers":     "Detected static type checkers (e.g. mypy, pyright, tsc).",
    "CI":               "Detected CI configuration files (e.g. GitHub Actions, CircleCI).",
    "Coverage":         "Coverage tool detection and source-to-test mapping summary.",
    "Uncovered source modules": "Source modules with no matching test file detected.",
    "Files":            "Per-file breakdown of eval case files: item targeted, case counts, fixtures.",
    "Item coverage":    "Whether each plugin/skill has at least one eval case targeting it.",
    "Case quality issues": "Eval files with structural problems (e.g. missing triggers, missing fixtures).",
    "Skills":           "Per-SKILL.md breakdown: frontmatter, description, and line count.",
    "Oversized prompts": "Markdown prompts over the 300-line readability threshold.",
    "Description-task quality": "Per-task specificity (and Locate path match) for the four description-shaped benchmark tasks.",
    "Duplicate-named directory pairs": "Directory pairs whose names map to the same canonical bucket (e.g. util/shared/common).",
    "Generated dirs at repo root": "Generated/build directories present at the repo root (clutter top-level listings).",
    # Chip glossary keys (referenced via tip())
    "chip-gitignore":   "Whether a .gitignore file is present at the repo root.",
    "chip-secret-hits": "Count of tracked files matching secret patterns (API keys, tokens, etc.).",
    "chip-big-binaries": "Count of large binary files tracked in git (LFS candidates).",
    "chip-evals-present": "An evals/ directory exists at the repo root.",
    "chip-case-files":  "Number of eval case files found under evals/.",
    "chip-total-cases": "Total number of declared test cases across all eval files.",
    "chip-item-coverage": "Plugins/skills that have at least one eval case targeting them, out of the total.",
    "chip-case-quality": "Eval files with structural quality issues (missing triggers, fixtures, etc.).",
    "chip-skill-count": "Number of SKILL.md files detected.",
    "chip-missing-description": "SKILL.md files missing a frontmatter description field.",
    "chip-over-skill-lines": "SKILL.md files exceeding the ~200-line readability cap.",
    "chip-md-files":    "Total markdown files considered for prompt hygiene.",
    "chip-total-lines": "Sum of lines across all considered markdown files.",
    "chip-over-prompt-lines": "Markdown files over the 300-line readability threshold.",
    "chip-root-dirs":   "Number of entries at the repo root (high counts hurt agent walkability).",
    "chip-dup-names":   "Directory pairs whose names collapse to the same bucket (util/shared/common, etc.).",
    "chip-generated":   "Generated/build directories present at the repo root.",
    "chip-prefix-collision": "Common filename prefix shared by a large fraction of files (e.g. all start with the same word).",
    "chip-signals-score": "Static walkability signals satisfied, out of 4.",
    "chip-coverage-tool": "Detected coverage tool and its configuration source.",
    "chip-source-test-map": "Ratio of source modules that have a corresponding test file.",
    "chip-lines-limit": "Instruction file line count vs. the configured soft limit.",
    "chip-nested-instructions": "CLAUDE.md / AGENTS.md files found below the repo root — enables layered, scoped context.",
    "chip-settings-json": ".claude/settings.json present at the repo root.",
    "chip-deny-rules":  "Number of permissions.deny rules — excludes noise (generated files, vendored code) from agent reads.",
    "chip-hooks":       "Files under .claude/hooks/ — event-driven automation (Stop, SessionStart, PreToolUse, etc.).",
    "chip-mcp-servers": "MCP servers declared in .mcp.json — surfaces internal tools, docs, and APIs to the agent.",
    "chip-codebase-map": "ARCHITECTURE.md / STRUCTURE.md / README with a top-level dir map — compensates for unconventional layouts.",
    "chip-instructions-age": "Days since the root instruction file was last touched in git history.",
    "chip-config-score": "Number of agent-runtime affordances configured (nested instructions, deny rules, hooks, MCP servers, map), out of 5.",
    "chip-commands-doc": "Whether the instructions document common project commands.",
    "chip-gotchas":     "Whether the instructions call out known gotchas or pitfalls.",
    "chip-shape":       "Detected repo shape (code repo vs. agent harness) that determines which pillars apply.",
    "chip-instruction-coverage": "Whether at least one of CLAUDE.md / AGENTS.md is present.",
    # Hero / scorecard
    "grade-letter":              "Overall agentic-readiness grade (A–F) summarizing the per-dimension scores below.",
    # Specificity chip values (used in Benchmark + Walkability tables)
    "high":                      "High specificity — the response named concrete files/symbols and matched the expected count.",
    "medium":                    "Medium specificity — partial answer with hedging or rough counts.",
    "low":                       "Low specificity — vague generalities without concrete names.",
    # Locate path-match chips
    "match":                     "Locate task: the agent's answer matched the pre-computed expected path.",
    "miss":                      "Locate task: the agent's answer did not match the expected path.",
    # Cost overview chips
    "chip-cost-orchestrator":    "Model that ran the orchestrator session for this audit.",
    "chip-cost-subagents":       "Model the benchmark subagents ran on, and total USD billed across those sessions.",
    "chip-cost-cheapest":        "Cheapest projected cost across principal models, applying their list rates to this audit's token profile.",
}


def tip(key: str) -> str:
    """Return a ` title="..."` attribute (with leading space) for `key`, or empty string."""
    text = GLOSSARY.get(key)
    return f' title="{e(text)}"' if text else ""


def tone_for_outcome(outcome: str) -> str:
    o = outcome.lower()
    if o.startswith("complete"):
        return "ok"
    if o.startswith("skipped"):
        return "warn"
    return "bad"


def tone_for_grade(grade: str) -> str:
    g = (grade or "").strip().upper()[:1]
    if g == "A":
        return "ok"
    if g == "B":
        return "warn"
    if g in ("C", "D", "F"):
        return "bad"
    return ""


def ibar(pct: float, label: str, tone: str = "", highlight: bool = False) -> str:
    pct = max(0.0, min(100.0, float(pct)))
    cls = f" {tone}" if tone else ""
    hi = " highlight" if highlight else ""
    return (
        f'<span class="ibar{hi}"><span class="track">'
        f'<span class="fill{cls}" style="width:{pct:.1f}%"></span></span>'
        f'<span class="val">{e(label)}</span></span>'
    )


# ---- section renderers ---------------------------------------------------

def render_top_header(repo_path: str, ts: str, iso_ts: str) -> str:
    name = Path(repo_path).name or repo_path
    toggle = (
        '<button class="theme-toggle" type="button" id="theme-toggle" '
        'aria-label="Switch theme" title="Switch theme">'
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
        f'<span class="repo">{e(name)}</span>'
        f'<span class="path"><span class="sr-only">Repo path: </span>{e(repo_path)}</span>'
        f'<button class="copy" data-path="{e(repo_path)}" type="button" '
        f'aria-label="Copy repo path to clipboard" title="Copy path">copy path</button>'
        f'<time class="ts" datetime="{e(iso_ts)}">'
        f'<span class="sr-only">Generated </span>{e(ts)}</time>'
        f'{toggle}'
        f'</header>'
    )


def render_scorecard(
    matrix: dict,
    static: dict,
    usage: dict,
    actual_model: str | None,
    orchestrator_model: str | None,
    warnings: list[str],
) -> str:
    grade = matrix.get("grade", "?")
    tone = tone_for_grade(grade)
    rationale = matrix.get("rationale", "")
    scores = matrix.get("scores", [])

    # KPI strip
    sessions = usage.get("sessions") or {}
    total_usd = 0.0
    total_wall = 0.0
    total_tasks = 0
    completed = 0
    for task in matrix.get("benchmark", []):
        u = sessions.get(task.get("session_id") or "", {})
        total_usd += float(u.get("usd") or 0.0)
        total_wall += float(task.get("wall_clock_s") or 0.0)
        total_tasks += 1
        if str(task.get("outcome", "")).lower().startswith("complete"):
            completed += 1
    est_tokens = int(static.get("repo_profile", {}).get("est_tokens", 0) or 0)

    # Cheapest projection across models (for KPI)
    totals = _benchmark_totals(matrix.get("benchmark", []), usage)
    cheapest_mid, cheapest_usd = None, None
    for mid in PROJECTION_RATES:
        v = project_usd(totals, mid)
        if v is None:
            continue
        if cheapest_usd is None or v < cheapest_usd:
            cheapest_mid, cheapest_usd = mid, v

    run_sub = f"Subagents on {friendly_model(actual_model)}" if actual_model else "Actual run"
    if orchestrator_model and actual_model and orchestrator_model != actual_model:
        run_sub = f"Subagents on {friendly_model(actual_model)} · orch. {friendly_model(orchestrator_model)}"
    kpis = [
        ("Run cost", fmt_usd(total_usd), run_sub),
        ("Cheapest model",
         fmt_usd(cheapest_usd) if cheapest_usd is not None else "—",
         friendly_model(cheapest_mid) if cheapest_mid else "projection"),
        ("Wall time", fmt_secs(total_wall),
         f"{completed} of {total_tasks} {'task' if total_tasks == 1 else 'tasks'} ok"),
        ("Repo size", fmt_tokens(est_tokens),
         fmt_count(static.get("repo_profile", {}).get("tracked_files"), "tracked file")),
    ]
    kpi_html = "".join(
        f'<div class="kpi"><div class="label"{tip(lbl)}>{e(lbl)}</div>'
        f'<div class="value">{val}</div><div class="sub">{e(sub)}</div></div>'
        for lbl, val, sub in kpis
    )

    # Dimension cards
    grade_to_pct = {"A": 100, "B": 75, "C": 50, "D": 25, "F": 10}
    dims = []
    for s in scores:
        t = (s.get("tone") or "").lower()
        g = (s.get("grade") or "?").strip().upper()[:1]
        pct = grade_to_pct.get(g, 50)
        dims.append(
            f'<div class="dim tone-{e(t)}">'
            f'<div class="row"><span class="label"{tip(s.get("label",""))}>{e(s.get("label",""))}</span>'
            f'<span class="badge">{e(s.get("grade",""))}</span></div>'
            f'<div class="bar"><i style="width:{pct}%"></i></div>'
            f'</div>'
        )
    dims_html = f'<div class="dims">{"".join(dims)}</div>' if dims else ""

    warn_html = ""
    if warnings:
        items = "".join(f"<li>{e(w)}</li>" for w in warnings)
        warn_html = (
            f'<div class="warnings"><div class="warn-title">'
            f'Degraded run · {len(warnings)} warning{"s" if len(warnings) != 1 else ""}</div>'
            f'<ul>{items}</ul></div>'
        )

    shape = (static.get("repo_shape") or {}).get("shape") or "code"
    shape_label = "agent harness" if shape == "agent-harness" else "code repo"
    shape_chip = f'<span class="chip ok" style="margin-left:8px"{tip("chip-shape")}>shape · {e(shape_label)}</span>'

    return f"""
<section id="scorecard">
  <h2{tip('Scorecard')}>Scorecard</h2>
  <div class="hero tone-{e(tone)}">
    <div class="grade"{tip('grade-letter')}>{e(grade)}</div>
    <div>
      <div class="rationale">{e(rationale)}{shape_chip}</div>
      <div class="kpis">{kpi_html}</div>
    </div>
  </div>
  {dims_html}
  {warn_html}
</section>
""".strip()


def render_repo_profile(profile: dict) -> str:
    paths = profile.get("heaviest_paths", []) or []
    dirs = profile.get("heaviest_dirs", []) or []
    max_path = max((int(p.get("est_tokens", 0) or 0) for p in paths), default=1) or 1
    max_dir = max((int(d.get("est_tokens", 0) or 0) for d in dirs), default=1) or 1

    def path_row(p: dict) -> str:
        tok = int(p.get("est_tokens", 0) or 0)
        pct = 100.0 * tok / max_path
        return (
            f"<tr><td>{e(p['path'])}</td>"
            f"<td class='num'>{fmt_bytes(p['bytes'])}</td>"
            f"<td class='bar-cell'>{ibar(pct, fmt_tokens(tok))}</td></tr>"
        )

    def dir_row(d: dict) -> str:
        tok = int(d.get("est_tokens", 0) or 0)
        pct = 100.0 * tok / max_dir
        return (
            f"<tr><td>{e(d['dir'])}</td>"
            f"<td class='num'>{fmt_bytes(d['bytes'])}</td>"
            f"<td class='bar-cell'>{ibar(pct, fmt_tokens(tok))}</td></tr>"
        )

    head_paths = paths[:5]
    tail_paths = paths[5:]
    paths_table = (
        f"<table><thead><tr><th{tip('Path')}>Path</th><th class='num'{tip('Bytes')}>Bytes</th>"
        f"<th{tip('Est. tokens')}>Est. tokens</th></tr></thead><tbody>"
        + "".join(path_row(p) for p in head_paths)
        + "</tbody></table>"
    )
    if tail_paths:
        paths_table += (
            f'<details class="more"><summary>{len(tail_paths)} more</summary>'
            "<table><tbody>"
            + "".join(path_row(p) for p in tail_paths)
            + "</tbody></table></details>"
        )

    dirs_table = (
        f"<table><thead><tr><th{tip('Dir')}>Dir</th><th class='num'{tip('Bytes')}>Bytes</th>"
        f"<th{tip('Est. tokens')}>Est. tokens</th></tr></thead><tbody>"
        + "".join(dir_row(d) for d in dirs)
        + "</tbody></table>"
    )

    return f"""
<section id="repo-profile">
  <h2{tip('Repo profile')}>Repo profile</h2>
  <div class="chiprow">
    <span class="chip" title="Files tracked by git in this repo.">{fmt_count(profile.get('tracked_files'), 'tracked file')}</span>
    <span class="chip" title="Text (non-binary) files among tracked files.">{fmt_count(profile.get('text_files'), 'text file')}</span>
    <span class="chip" title="Heuristic token estimate for the full text content (~4 chars per token).">~{fmt_tokens(profile.get('est_tokens'))}</span>
  </div>
  <h3{tip('Heaviest paths')}>Heaviest paths</h3>
  {paths_table}
  <h3{tip('Heaviest top-level dirs')}>Heaviest top-level dirs</h3>
  {dirs_table}
</section>
""".strip()


def render_instructions(inst: dict) -> str:
    parity_key = inst.get("mirror_parity", "n/a")
    parity = MIRROR_PARITY_LABEL.get(parity_key, parity_key)
    # Either file alone is acceptable; only missing-both is a failure.
    parity_tone = {
        "in-sync":      "ok",
        "claude-only":  "ok",
        "agents-only":  "ok",
        "drift":        "warn",
        "missing-both": "bad",
    }.get(parity_key, "warn")

    claude_present = bool(inst.get("claude_md"))
    agents_present = bool(inst.get("agents_md"))
    any_present = claude_present or agents_present

    def file_block(label: str, info: dict | None, other_present: bool) -> str:
        if not info:
            # Missing is only "bad" when neither file exists — if the other
            # one is present, this is an informational gap, not a failure.
            tone = "warn" if other_present else "bad"
            note = "absent (other present)" if other_present else "missing"
            return f'<span class="chip {tone}">{e(label)} · {note}</span>'
        gotchas = "ok" if info.get("mentions_gotchas") else "warn"
        lines = info.get("lines", 0)
        limit = info.get("line_limit")
        over = info.get("over_line_limit")
        if limit is None:
            len_chip = ""
        elif over:
            len_chip = (
                f'<span class="chip warn"{tip("chip-lines-limit")}>{fmt_int(lines)} / {fmt_int(limit)} lines (over limit)</span>'
            )
        else:
            len_chip = (
                f'<span class="chip ok"{tip("chip-lines-limit")}>{fmt_int(lines)} / {fmt_int(limit)} lines</span>'
            )
        cmds_ok = info.get("mentions_commands")
        cmd_chip = (
            f'<span class="chip {"ok" if cmds_ok else "warn"}"{tip("chip-commands-doc")}>'
            f'Commands {"documented" if cmds_ok else "not listed"}</span>'
        )
        return (
            f'<span class="chip ok" title="Instruction file size, line count, and heading count.">{e(label)} · {fmt_bytes(info["bytes"])} · '
            f'{fmt_count(info["lines"], "line")} · '
            f'{fmt_count(info["headings"], "heading")}</span>'
            f'{len_chip}{cmd_chip}'
            f'<span class="chip {gotchas}"{tip("chip-gotchas")}>Gotchas '
            f'{"called out" if info.get("mentions_gotchas") else "not called out"}</span>'
        )

    coverage_tone = "ok" if any_present else "bad"
    coverage_label = "At least one instruction file present" if any_present else "No instruction files found"

    return f"""
<section id="instructions">
  <h2{tip('Instructions')}>Agent instructions</h2>
  <div class="chiprow"><span class="chip {coverage_tone}"{tip('chip-instruction-coverage')}>{coverage_label}</span></div>
  <div class="chiprow">{file_block("CLAUDE.md", inst.get("claude_md"), agents_present)}</div>
  <div class="chiprow">{file_block("AGENTS.md", inst.get("agents_md"), claude_present)}</div>
  <div class="chiprow"><span class="chip {parity_tone}"{tip(parity_key)}>{e(parity)}</span></div>
</section>
""".strip()


def render_agent_config(cfg: dict) -> str:
    nested = cfg.get("nested_instructions") or []
    hooks = cfg.get("hooks") or []
    mcp = cfg.get("mcp_servers") or []
    has_settings = bool(cfg.get("has_settings_json"))
    deny = int(cfg.get("deny_rules") or 0)
    allow = int(cfg.get("allow_rules") or 0)
    map_path = cfg.get("codebase_map_path")
    age = cfg.get("instructions_age_days")
    score = int(cfg.get("config_score") or 0)

    def tone(b: bool) -> str:
        return "ok" if b else "warn"

    chips: list[str] = []
    chips.append(
        f'<span class="chip {tone(bool(nested))}"{tip("chip-nested-instructions")}>'
        f'nested instructions · {len(nested)}</span>'
    )
    chips.append(
        f'<span class="chip {tone(has_settings)}"{tip("chip-settings-json")}>'
        f'.claude/settings.json · {"present" if has_settings else "missing"}</span>'
    )
    chips.append(
        f'<span class="chip {tone(deny > 0)}"{tip("chip-deny-rules")}>'
        f'deny rules · {deny}</span>'
    )
    if allow:
        chips.append(
            f'<span class="chip ok"{tip("chip-deny-rules")}>allow rules · {allow}</span>'
        )
    chips.append(
        f'<span class="chip {tone(bool(hooks))}"{tip("chip-hooks")}>'
        f'hooks · {len(hooks)}</span>'
    )
    chips.append(
        f'<span class="chip {tone(bool(mcp))}"{tip("chip-mcp-servers")}>'
        f'MCP servers · {len(mcp)}</span>'
    )
    chips.append(
        f'<span class="chip {tone(bool(map_path))}"{tip("chip-codebase-map")}>'
        f'codebase map · {e(map_path) if map_path else "missing"}</span>'
    )
    if age is None:
        age_chip = f'<span class="chip warn"{tip("chip-instructions-age")}>instructions age · unknown</span>'
    else:
        age_tone = "ok" if age <= 180 else ("warn" if age <= 365 else "bad")
        age_chip = f'<span class="chip {age_tone}"{tip("chip-instructions-age")}>instructions age · {age}d</span>'
    chips.append(age_chip)
    score_tone = "ok" if score >= 3 else ("warn" if score >= 1 else "bad")
    chips.append(
        f'<span class="chip {score_tone}"{tip("chip-config-score")}>'
        f'config score · {score}/5</span>'
    )

    extras: list[str] = []
    if nested:
        rows = "".join(
            f"<tr><td>{e(n['path'])}</td><td class='num'>{int(n['lines'])}</td>"
            f"<td>{'yes' if n.get('mentions_commands') else 'no'}</td></tr>"
            for n in nested
        )
        extras.append(
            f"<h3{tip('chip-nested-instructions')}>Nested instruction files</h3>"
            f"<table><thead><tr><th{tip('Path')}>Path</th>"
            f"<th class='num'{tip('Lines')}>Lines</th>"
            f"<th>Commands?</th></tr></thead><tbody>{rows}</tbody></table>"
        )
    if hooks:
        rows = "".join(f"<tr><td>{e(h)}</td></tr>" for h in hooks)
        extras.append(
            f"<h3{tip('chip-hooks')}>Hooks</h3>"
            f"<table><thead><tr><th>File</th></tr></thead><tbody>{rows}</tbody></table>"
        )
    if mcp:
        rows = "".join(f"<tr><td>{e(s)}</td></tr>" for s in mcp)
        extras.append(
            f"<h3{tip('chip-mcp-servers')}>MCP servers</h3>"
            f"<table><thead><tr><th>Name</th></tr></thead><tbody>{rows}</tbody></table>"
        )

    body = f'<div class="chiprow">{"".join(chips)}</div>{"".join(extras)}'
    return f'<section id="agent-config"><h2{tip("Agent config")}>Agent config</h2>{body}</section>'


def render_tests(tests: dict) -> str:
    def chip_row(items: list[str], tone: str) -> str:
        if not items:
            return '<span class="chip bad">none detected</span>'
        return "".join(f'<span class="chip {tone}">{e(x)}</span>' for x in items)

    cov_tool = tests.get("coverage_tool")
    if cov_tool:
        thr = cov_tool.get("threshold")
        thr_txt = f" · threshold {thr}%" if isinstance(thr, int) else ""
        cov_chip = (
            f'<span class="chip ok"{tip("chip-coverage-tool")}>coverage tool · {e(cov_tool.get("tool", "?"))}'
            f' ({e(cov_tool.get("source", "?"))})' + thr_txt + '</span>'
        )
    else:
        cov_chip = f'<span class="chip warn"{tip("chip-coverage-tool")}>coverage tool · not detected</span>'

    mapping = tests.get("source_test_mapping") or {}
    n_src = int(mapping.get("n_source") or 0)
    n_w   = int(mapping.get("n_with_test") or 0)
    ratio = float(mapping.get("coverage_ratio") or 0.0)
    uncov = mapping.get("uncovered_modules") or []
    if n_src == 0:
        map_chip = f'<span class="chip warn"{tip("chip-source-test-map")}>source ↔ test · no test files found</span>'
        map_table = ""
    else:
        tone = "ok" if ratio >= 0.7 else ("warn" if ratio >= 0.3 else "bad")
        map_chip = (
            f'<span class="chip {tone}"{tip("chip-source-test-map")}>source ↔ test · {n_w}/{n_src} '
            f'({int(ratio*100)}%)</span>'
        )
        if uncov:
            rows = "".join(f"<tr><td>{e(m)}</td></tr>" for m in uncov)
            map_table = (
                f"<h3{tip('Uncovered source modules')}>Uncovered source modules (first {len(uncov)})</h3>"
                f"<table><thead><tr><th{tip('Path')}>Path</th></tr></thead><tbody>{rows}</tbody></table>"
            )
        else:
            map_table = ""

    return f"""
<section id="tests">
  <h2{tip('Tests')}>Tests &amp; harness</h2>
  <h3{tip('Runners')}>Runners</h3><div class="chiprow">{chip_row(tests.get("runners", []), "ok")}</div>
  <h3{tip('Linters')}>Linters</h3><div class="chiprow">{chip_row(tests.get("linters", []), "ok")}</div>
  <h3{tip('Typecheckers')}>Typecheckers</h3><div class="chiprow">{chip_row(tests.get("typecheckers", []), "ok")}</div>
  <h3{tip('CI')}>CI</h3><div class="chiprow">{chip_row(tests.get("ci_configs", []), "ok")}</div>
  <h3{tip('Coverage')}>Coverage</h3><div class="chiprow">{cov_chip}{map_chip}</div>
  {map_table}
</section>
""".strip()


def render_hygiene(hyg: dict) -> str:
    gi = hyg.get("gitignore_present")
    gi_chip = f'<span class="chip {"ok" if gi else "bad"}"{tip("chip-gitignore")}>.gitignore · {"present" if gi else "missing"}</span>'
    secrets = hyg.get("secret_hits", []) or []
    secrets_chip = f'<span class="chip {"bad" if secrets else "ok"}"{tip("chip-secret-hits")}>secret-pattern hits · {len(secrets)}</span>'
    binaries = hyg.get("big_binaries", []) or []
    bin_chip = f'<span class="chip {"warn" if binaries else "ok"}"{tip("chip-big-binaries")}>big binaries · {len(binaries)}</span>'
    blocks = [f'<div class="chiprow">{gi_chip}{secrets_chip}{bin_chip}</div>']
    if secrets:
        rows = "".join(f"<tr><td>{e(s['path'])}</td><td>{e(s['kind'])}</td></tr>" for s in secrets)
        blocks.append(f"<h3{tip('Secret-pattern hits')}>Secret-pattern hits</h3><table><thead><tr><th{tip('Path')}>Path</th><th{tip('Kind')}>Kind</th></tr></thead><tbody>{rows}</tbody></table>")
    if binaries:
        rows = "".join(f"<tr><td>{e(b['path'])}</td><td class='num'>{fmt_bytes(b['bytes'])}</td></tr>" for b in binaries)
        blocks.append(f"<h3{tip('Big binaries')}>Big binaries</h3><table><thead><tr><th{tip('Path')}>Path</th><th class='num'{tip('Bytes')}>Bytes</th></tr></thead><tbody>{rows}</tbody></table>")
    return f'<section id="hygiene"><h2{tip("Hygiene")}>Hygiene</h2>{"".join(blocks)}</section>'


def _signal_section(section_id: str, title: str, signals: list[str], tip_key: str) -> str:
    if not signals:
        chips = '<span class="chip bad">none detected</span>'
    else:
        chips = "".join(f'<span class="chip ok">{e(s)}</span>' for s in signals)
    return f'<section id="{section_id}"><h2{tip(tip_key)}>{title}</h2><div class="chiprow">{chips}</div></section>'


def render_dev_env(d: dict) -> str:
    return _signal_section("dev-env", "Dev environment", d.get("signals", []) or [], "Dev environment")


def render_observability(d: dict) -> str:
    return _signal_section("observability", "Observability", d.get("signals", []) or [], "Observability")


def render_security(d: dict) -> str:
    return _signal_section("security", "Security &amp; governance", d.get("signals", []) or [], "Security")


def render_evals(ev: dict) -> str:
    has_dir = ev.get("has_dir")
    n_files = int(ev.get("n_files") or 0)
    n_cases = int(ev.get("n_total_cases") or 0)
    files = ev.get("files") or []
    coverage = ev.get("coverage") or []
    uncovered = ev.get("uncovered_items") or []
    quality_issues = ev.get("quality_issues") or []
    if not has_dir:
        chips = '<span class="chip bad"{0}>no <code>evals/</code> directory</span>'.format(tip("chip-evals-present"))
        body = f'<div class="chiprow">{chips}</div>'
        return f'<section id="evals"><h2{tip("Evals")}>Evals</h2>{body}</section>'
    chips = (
        f'<span class="chip ok"{tip("chip-evals-present")}>evals/ · present</span>'
        f'<span class="chip {"ok" if n_files else "bad"}"{tip("chip-case-files")}>case files · {n_files}</span>'
        f'<span class="chip {"ok" if n_cases else "warn"}"{tip("chip-total-cases")}>total cases · {n_cases}</span>'
    )
    if coverage:
        n_cov = sum(1 for c in coverage if c.get("covered"))
        cov_tone = "ok" if not uncovered else ("warn" if len(uncovered) <= len(coverage) // 2 else "bad")
        chips += (
            f'<span class="chip {cov_tone}"{tip("chip-item-coverage")}>item coverage · {n_cov}/{len(coverage)}</span>'
        )
    q_tone = "ok" if not quality_issues else "warn"
    chips += f'<span class="chip {q_tone}"{tip("chip-case-quality")}>case quality issues · {len(quality_issues)}</span>'
    body = f'<div class="chiprow">{chips}</div>'
    if files:
        rows = "".join(
            "<tr>"
            f"<td>{e(f['path'])}</td>"
            f"<td>{e(f.get('item') or '—')}</td>"
            f"<td class='num'>{int(f.get('n_cases') or 0)}</td>"
            f"<td>{'yes' if f.get('has_triggers') else 'no'}</td>"
            f"<td class='num'>{int(f.get('n_positive') or 0)}/{int(f.get('n_negative') or 0)}</td>"
            f"<td class='num'>{int(f.get('n_output_assertions') or 0)}</td>"
            f"<td class='num'>{len(f.get('fixtures_missing') or [])}</td>"
            "</tr>"
            for f in files
        )
        body += (
            f"<h3{tip('Files')}>Files</h3><table><thead><tr>"
            f"<th{tip('Path')}>Path</th><th{tip('Item')}>Item</th><th class='num'{tip('Cases')}>Cases</th>"
            f"<th{tip('Triggers')}>Triggers</th><th class='num'{tip('+/-')}>+/-</th>"
            f"<th class='num'{tip('Output')}>Output</th><th class='num'{tip('Missing fixtures')}>Missing fixtures</th>"
            f"</tr></thead><tbody>{rows}</tbody></table>"
        )
    if coverage:
        rows = "".join(
            f"<tr><td>{e(c['item'])}</td><td>{'✓' if c.get('covered') else '✗'}</td></tr>"
            for c in coverage
        )
        body += (
            f"<h3{tip('Item coverage')}>Item coverage</h3><table><thead><tr>"
            f"<th{tip('Plugin / skill')}>Plugin / skill</th><th{tip('Eval present')}>Eval present</th>"
            f"</tr></thead><tbody>{rows}</tbody></table>"
        )
    if quality_issues:
        rows = "".join(
            f"<tr><td>{e(q['path'])}</td><td>{e(', '.join(q.get('problems') or []))}</td></tr>"
            for q in quality_issues
        )
        body += (
            f"<h3{tip('Case quality issues')}>Case quality issues</h3><table><thead><tr>"
            f"<th{tip('Path')}>Path</th><th{tip('Problems')}>Problems</th>"
            f"</tr></thead><tbody>{rows}</tbody></table>"
        )
    return f'<section id="evals"><h2{tip("Evals")}>Evals</h2>{body}</section>'


def render_skill_quality(sq: dict) -> str:
    n = int(sq.get("n_skills") or 0)
    missing = sq.get("skills_missing_description") or []
    over = sq.get("skills_over_line_limit") or []
    skills = sq.get("skills") or []
    if n == 0:
        chips = f'<span class="chip bad"{tip("chip-skill-count")}>no <code>SKILL.md</code> files</span>'
        body = f'<div class="chiprow">{chips}</div>'
    else:
        chips = (
            f'<span class="chip ok"{tip("chip-skill-count")}>SKILL.md · {n}</span>'
            f'<span class="chip {"warn" if missing else "ok"}"{tip("chip-missing-description")}>missing description · {len(missing)}</span>'
            f'<span class="chip {"warn" if over else "ok"}"{tip("chip-over-skill-lines")}>over ~200 lines · {len(over)}</span>'
        )
        body = f'<div class="chiprow">{chips}</div>'
        if skills:
            rows = "".join(
                "<tr>"
                f"<td>{e(s['path'])}</td>"
                f"<td>{'yes' if s.get('has_frontmatter') else 'no'}</td>"
                f"<td>{'yes' if s.get('has_description') else 'no'}</td>"
                f"<td class='num'>{int(s.get('line_count') or 0)}</td>"
                "</tr>"
                for s in skills
            )
            body += (
                f"<h3{tip('Skills')}>Skills</h3><table><thead><tr><th{tip('Path')}>Path</th>"
                f"<th{tip('Frontmatter')}>Frontmatter</th><th{tip('Description')}>Description</th>"
                f"<th class='num'{tip('Lines')}>Lines</th></tr></thead>"
                f"<tbody>{rows}</tbody></table>"
            )
    return f'<section id="skill-quality"><h2{tip("Skill quality")}>Skill quality</h2>{body}</section>'


def render_prompt_hygiene(ph: dict) -> str:
    n_md = int(ph.get("n_md_files") or 0)
    total_lines = int(ph.get("total_lines") or 0)
    oversized = ph.get("oversized") or []
    chips = (
        f'<span class="chip ok"{tip("chip-md-files")}>markdown files · {n_md}</span>'
        f'<span class="chip ok"{tip("chip-total-lines")}>total lines · {total_lines}</span>'
        f'<span class="chip {"warn" if oversized else "ok"}"{tip("chip-over-prompt-lines")}>over 300 lines · {len(oversized)}</span>'
    )
    body = f'<div class="chiprow">{chips}</div>'
    if oversized:
        rows = "".join(
            f"<tr><td>{e(o['path'])}</td><td class='num'>{int(o.get('lines') or 0)}</td></tr>"
            for o in oversized
        )
        body += (
            f"<h3{tip('Oversized prompts')}>Oversized prompts</h3><table><thead><tr><th{tip('Path')}>Path</th>"
            f"<th class='num'{tip('Lines')}>Lines</th></tr></thead>"
            f"<tbody>{rows}</tbody></table>"
        )
    return f'<section id="prompt-hygiene"><h2{tip("Prompt hygiene")}>Prompt hygiene</h2>{body}</section>'


_WALK_DESC_TASKS = {"Repo walk", "Locate", "Trace", "Spot"}


def render_walkability(walk: dict, benchmark: list[dict]) -> str:
    """Render the Walkability pillar: static signals + per-task qualitative
    scores from the four description-shaped benchmark tasks."""
    root_count = int(walk.get("root_dir_count") or 0)
    dup_pairs = walk.get("duplicate_name_pairs") or []
    generated = walk.get("generated_dirs_present") or []
    prefix_token = walk.get("prefix_collision_token")
    prefix_ratio = float(walk.get("prefix_collision_ratio") or 0.0)
    signals_score = int(walk.get("signals_score") or 0)

    def tone(n: int, warn_at: int, bad_at: int) -> str:
        if n >= bad_at:
            return "bad"
        if n >= warn_at:
            return "warn"
        return "ok"

    chips = (
        f'<span class="chip {tone(root_count, 10, 15)}"{tip("chip-root-dirs")}>root dirs · {root_count}</span>'
        f'<span class="chip {"bad" if len(dup_pairs) >= 2 else ("warn" if dup_pairs else "ok")}"{tip("chip-dup-names")}>duplicate-name pairs · {len(dup_pairs)}</span>'
        f'<span class="chip {"warn" if generated else "ok"}"{tip("chip-generated")}>generated at root · {len(generated)}</span>'
        f'<span class="chip {"warn" if prefix_token else "ok"}"{tip("chip-prefix-collision")}>filename prefix collision · {prefix_token + " (" + str(int(prefix_ratio * 100)) + "%)" if prefix_token else "none"}</span>'
        f'<span class="chip"{tip("chip-signals-score")}>signals score · {signals_score}/4</span>'
    )
    body = f'<div class="chiprow">{chips}</div>'

    extras: list[str] = []
    if dup_pairs:
        rows = "".join(
            f"<tr><td>{e(p[0])}</td><td>{e(p[1])}</td></tr>" for p in dup_pairs
        )
        extras.append(
            f"<h3{tip('Duplicate-named directory pairs')}>Duplicate-named directory pairs</h3>"
            "<p class='muted'>Both names map to the same canonical bucket (e.g. <span class='kbd'>util</span>/<span class='kbd'>shared</span>/<span class='kbd'>common</span>) — agents can't tell which one owns what without reading both.</p>"
            f"<table><thead><tr><th>Dir A</th><th>Dir B</th></tr></thead><tbody>{rows}</tbody></table>"
        )
    if generated:
        items = "".join(f"<li><span class='kbd'>{e(g)}</span></li>" for g in generated)
        extras.append(
            f"<h3{tip('Generated dirs at repo root')}>Generated dirs at repo root</h3>"
            "<p class='muted'>These crowd the top-level listing on every agent read.</p>"
            f"<ul>{items}</ul>"
        )

    # Per-task panel: pull specificity + (for Locate) path_match from the matrix.
    task_rows = []
    for task in benchmark:
        name = task.get("task", "")
        if name not in _WALK_DESC_TASKS:
            continue
        spec = (task.get("specificity") or "—").lower()
        spec_tone = {"high": "ok", "medium": "warn", "low": "bad"}.get(spec, "")
        outcome = task.get("outcome", "")
        path_match = task.get("path_match")
        match_cell = ""
        if name == "Locate":
            if path_match is True:
                match_cell = f"<span class='chip ok'{tip('match')}>match</span>"
            elif path_match is False:
                match_cell = f"<span class='chip bad'{tip('miss')}>miss</span>"
            else:
                match_cell = "<span class='muted'>—</span>"
        else:
            match_cell = "<span class='muted'>n/a</span>"
        outcome_tip = tip(outcome.lower().split(":", 1)[0].strip()) or f' title="{e(outcome)}"'
        task_rows.append(
            "<tr>"
            f"<td>{e(name)}</td>"
            f"<td><span class='chip {tone_for_outcome(outcome)}'{outcome_tip}>{e(outcome)}</span></td>"
            f"<td><span class='chip {spec_tone}'{tip(spec)}>{e(spec)}</span></td>"
            f"<td>{match_cell}</td>"
            "</tr>"
        )
    if task_rows:
        extras.append(
            f"<h3{tip('Description-task quality')}>Description-task quality</h3>"
            "<p class='muted'>Specificity is the orchestrator's judgement after reading the subagent's final message: <em>high</em> = concrete names + correct count; <em>medium</em> = partial, some hedging; <em>low</em> = vague generalities. Locate also gets <em>match</em>/<em>miss</em> against the pre-computed answer path.</p>"
            "<table><thead><tr><th>Task</th><th>Outcome</th><th>Specificity</th><th>Path match</th></tr></thead>"
            f"<tbody>{''.join(task_rows)}</tbody></table>"
        )

    return f'<section id="walkability"><h2{tip("Walkability")}>Walkability</h2>{body}{"".join(extras)}</section>'


def render_benchmark(benchmark: list[dict], usage: dict, actual_model: str | None, orchestrator_model: str | None = None) -> str:
    sessions = usage.get("sessions", {})
    max_usd = max((float((sessions.get(t.get("session_id") or "", {}).get("usd") or 0.0)) for t in benchmark), default=0.0) or 1.0
    max_wall = max((float(t.get("wall_clock_s") or 0.0) for t in benchmark), default=0.0) or 1.0
    rows: list[str] = []
    for task in benchmark:
        sid = task.get("session_id") or ""
        u = sessions.get(sid, {})
        outcome = task.get("outcome", "")
        usd = float(u.get("usd") or 0.0)
        wall = float(task.get("wall_clock_s") or 0.0)
        usd_pct = 100.0 * usd / max_usd
        wall_pct = 100.0 * wall / max_wall
        spec = (task.get("specificity") or "").lower()
        spec_tone = {"high": "ok", "medium": "warn", "low": "bad"}.get(spec, "")
        spec_cell = (
            f"<span class='chip {spec_tone}'{tip(spec)}>{e(spec)}</span>" if spec
            else "<span class='muted'>—</span>"
        )
        rows.append(
            "<tr>"
            f"<td><span class='chip {tone_for_outcome(outcome)}'{tip(outcome.lower().split(':', 1)[0].strip()) or (' title=' + chr(34) + e(outcome) + chr(34))}>{e(outcome)}</span></td>"
            f"<td>{e(task.get('task',''))}</td>"
            f"<td>{spec_cell}</td>"
            f"<td class='num'>{fmt_tokens(u.get('input_tokens'))}</td>"
            f"<td class='num'>{fmt_tokens(u.get('output_tokens'))}</td>"
            f"<td class='num'>{fmt_tokens(u.get('cache_read_input_tokens'))}</td>"
            f"<td class='bar-cell'>{ibar(usd_pct, fmt_usd(usd))}</td>"
            f"<td class='bar-cell'>{ibar(wall_pct, fmt_secs(wall), tone='mute')}</td>"
            "</tr>"
        )
    if actual_model and orchestrator_model and orchestrator_model != actual_model:
        note = (
            f'<p class="muted">Orchestrator ran on <span class="kbd">{e(friendly_model(orchestrator_model))}</span>, but subagents recorded <span class="kbd">{e(friendly_model(actual_model))}</span> — likely because <span class="kbd">CLAUDE_CODE_SUBAGENT_MODEL</span> is set in the env (it overrides the Agent tool\'s <span class="kbd">model:</span> parameter and any frontmatter). To benchmark on the orchestrator\'s model, remove that entry from <span class="kbd">~/.claude/settings.json</span> under <span class="kbd">env</span> and restart Claude Code. Per-task cost is on the dispatched model; the Cost section projects what the same token profile would cost on other models.</p>'
        )
    elif actual_model:
        note = (
            f'<p class="muted">All tasks ran as subagents on <span class="kbd">{e(friendly_model(actual_model))}</span> (read from session logs). Per-task cost is on that model; the Cost section projects what the same token profile would cost on other models.</p>'
        )
    else:
        note = '<p class="muted">Per-task cost is on the subagent dispatch model. The Cost section projects what the same token profile would cost on other models.</p>'
    return (
        f'<section id="benchmark"><h2{tip("Benchmark")}>Benchmark</h2>'
        + note
        + "<table><thead><tr>"
        f"<th{tip('Outcome')}>Outcome</th><th{tip('Task')}>Task</th>"
        f"<th{tip('Specificity')}>Specificity</th>"
        f"<th class='num'{tip('In')}>In</th><th class='num'{tip('Out')}>Out</th>"
        f"<th class='num'{tip('Cache read')}>Cache read</th><th{tip('USD')}>USD</th><th{tip('Wall')}>Wall</th>"
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></section>"
    )


def _benchmark_totals(benchmark: list[dict], usage: dict) -> dict:
    sessions = usage.get("sessions", {})
    totals = {
        "input_tokens": 0, "output_tokens": 0,
        "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
    }
    for task in benchmark:
        u = sessions.get(task.get("session_id") or "", {})
        for k in totals:
            totals[k] += int(u.get(k, 0) or 0)
    return totals


def render_cost(benchmark: list[dict], usage: dict, est_tokens: int, actual_model: str | None, orchestrator_model: str | None = None) -> str:
    totals = _benchmark_totals(benchmark, usage)
    sessions = usage.get("sessions", {})
    actual_usd = sum(float(sessions.get(t.get("session_id") or "", {}).get("usd") or 0.0) for t in benchmark)

    # Projection rows with bars — warm (with cache discount) and cold (no cache benefit)
    projections = [
        (mid, project_usd(totals, mid), project_cold_usd(totals, mid))
        for mid in PROJECTION_RATES
    ]
    proj_max = max((v for _, v, _ in projections if v is not None), default=0.0) or 1.0
    cold_max = max((c for _, _, c in projections if c is not None), default=0.0) or 1.0
    priced = [(m, v) for m, v, _ in projections if v is not None]
    cheapest: tuple[str | None, float | None] = min(priced, key=lambda p: p[1]) if priced else (None, None)

    def proj_row(mid: str, v: float | None, cold: float | None) -> str:
        is_cheap = v is not None and cheapest[0] == mid
        is_actual = mid == actual_model
        marks = []
        if is_actual:
            marks.append(f'<span class="chip"{tip("actual run")}>actual run</span>')
        if is_cheap:
            marks.append(f'<span class="chip ok"{tip("cheapest")}>cheapest</span>')
        pct = 100.0 * (v / proj_max) if v is not None else 0.0
        cold_pct = 100.0 * (cold / cold_max) if cold is not None else 0.0
        tone = "ok" if is_cheap else ""
        return (
            f"<tr><td>{e(friendly_model(mid))} {' '.join(marks)}</td>"
            f"<td class='bar-cell'>{ibar(pct, fmt_usd(v), tone=tone, highlight=is_cheap)}</td>"
            f"<td class='bar-cell'>{ibar(cold_pct, fmt_usd(cold), tone='mute')}</td></tr>"
        )

    proj_rows = "".join(proj_row(m, v, c) for m, v, c in projections)

    # Load-cost rows with bars
    load_rows_data = [(mid, est_tokens * rate / 1_000_000.0) for mid, rate in LOAD_RATES_PER_M_INPUT.items()]
    load_max = max((v for _, v in load_rows_data), default=0.0) or 1.0
    load_rows = "".join(
        f"<tr><td>{e(friendly_model(mid))}</td>"
        f"<td class='num'>{fmt_tokens(est_tokens)}</td>"
        f"<td class='bar-cell'>{ibar(100.0 * v / load_max, fmt_usd(v))}</td></tr>"
        for mid, v in load_rows_data
    )

    actual_label = friendly_model(actual_model) if actual_model else "subagent dispatch model"
    orch_chip = (
        f'<span class="chip"{tip("chip-cost-orchestrator")}>Orchestrator · <span class="kbd">{e(friendly_model(orchestrator_model))}</span></span>'
        if orchestrator_model and orchestrator_model != actual_model else ""
    )

    return f"""
<section id="cost">
  <h2{tip('Cost')}>Cost</h2>
  <div class="chiprow">
    {orch_chip}
    <span class="chip"{tip('chip-cost-subagents')}>Subagents · <span class="kbd">{e(actual_label)}</span> · {fmt_usd(actual_usd)}</span>
    {('<span class="chip ok"' + tip('chip-cost-cheapest') + '>Cheapest projection · ' + e(friendly_model(cheapest[0]) if cheapest[0] else "?") + ' · ' + fmt_usd(cheapest[1]) + '</span>') if cheapest[1] is not None else '<span class="chip warn">Projection unavailable</span>'}
  </div>
  <h3>Projection · same token profile, each principal model</h3>
  <p class="muted">The benchmark fires one dispatch per task, so all four cells ran on <span class="kbd">{e(actual_label)}</span>. Below applies each model's list rates to that same token profile — useful for "what would this cost on Haiku?" estimates, but caching behaviour differs across models, so treat as a guide. <strong>Cold</strong> = no cache hits; what the first run (or the first run after the 5-minute prompt-cache TTL expires) costs at list input rate.</p>
  <table><thead><tr><th{tip('Model')}>Model</th><th{tip('Projected USD (warm)')}>Projected USD (warm)</th><th{tip('USD (cold)')}>USD (cold)</th></tr></thead><tbody>{proj_rows}</tbody></table>
  <h3>Est. cost to load full repo into context <span class="muted">(input-only, heuristic)</span></h3>
  <table><thead><tr><th{tip('Model')}>Model</th><th class='num'{tip('Est. tokens')}>Est. tokens</th><th{tip('USD')}>USD</th></tr></thead><tbody>{load_rows}</tbody></table>
</section>
""".strip()


def render_recommendations(recs: list[str]) -> str:
    items = "".join(f"<li>{e(r)}</li>" for r in recs[:5])
    if not items:
        items = '<li class="muted">No recommendations — repo looks ready.</li>'
    return f'<section id="recommendations"><h2{tip("Recommendations")}>Recommendations</h2><ol class="recs">{items}</ol></section>'


# ---- top-level ----------------------------------------------------------

def build_html(static: dict, usage: dict, matrix: dict) -> str:
    repo_path = matrix.get("repo_path") or str(Path.cwd())
    now = datetime.datetime.now()
    iso_ts = now.isoformat(timespec="seconds")
    ts = fmt_human_datetime(now)
    name = Path(repo_path).name or repo_path

    shape = (static.get("repo_shape") or {}).get("shape") or "code"
    if shape == "agent-harness":
        nav_links = [
            ("scorecard",       "Scorecard"),
            ("repo-profile",    "Repo profile"),
            ("walkability",     "Walkability"),
            ("instructions",    "Agent instructions"),
            ("agent-config",    "Agent config"),
            ("evals",           "Evals"),
            ("hygiene",         "Hygiene"),
            ("skill-quality",   "Skill quality"),
            ("prompt-hygiene",  "Prompt hygiene"),
            ("security",        "Security &amp; governance"),
            ("benchmark",       "Benchmark"),
            ("cost",            "Cost"),
            ("recommendations", "Recommendations"),
        ]
    else:
        nav_links = [
            ("scorecard",       "Scorecard"),
            ("repo-profile",    "Repo profile"),
            ("walkability",     "Walkability"),
            ("instructions",    "Agent instructions"),
            ("agent-config",    "Agent config"),
            ("tests",           "Tests &amp; harness"),
            ("hygiene",         "Hygiene"),
            ("dev-env",         "Dev environment"),
            ("observability",   "Observability"),
            ("security",        "Security &amp; governance"),
            ("benchmark",       "Benchmark"),
            ("cost",            "Cost"),
            ("recommendations", "Recommendations"),
        ]
    nav_html = "".join(f'<li><a href="#{i}">{lbl}</a></li>' for i, lbl in nav_links)

    warnings: list[str] = list(matrix.get("warnings") or [])
    sessions = usage.get("sessions") or {}
    missing = sum(1 for v in sessions.values() if v.get("status") != "ok")
    total = len(sessions)
    if total and missing:
        warnings.append(f"Usage attribution dropped for {missing}/{total} session(s) — see Benchmark.")

    actual_model: str | None = matrix.get("actual_model")
    if not actual_model:
        for v in sessions.values():
            if v.get("model"):
                actual_model = v["model"]
                break
    orchestrator_model: str | None = matrix.get("orchestrator_model")

    sections = [
        render_scorecard(matrix, static, usage, actual_model, orchestrator_model, warnings),
        render_repo_profile(static.get("repo_profile", {})),
        render_walkability(static.get("walkability", {}), matrix.get("benchmark", [])),
        render_instructions(static.get("agent_instructions", {})),
        render_agent_config(static.get("agent_config", {})),
    ]
    if shape == "agent-harness":
        sections += [
            render_evals(static.get("evals", {})),
            render_hygiene(static.get("hygiene", {})),
            render_skill_quality(static.get("skill_quality", {})),
            render_prompt_hygiene(static.get("prompt_hygiene", {})),
        ]
    else:
        sections += [
            render_tests(static.get("tests", {})),
            render_hygiene(static.get("hygiene", {})),
            render_dev_env(static.get("dev_env", {})),
            render_observability(static.get("observability", {})),
        ]
    sections += [
        render_security(static.get("security", {})),
        render_benchmark(matrix.get("benchmark", []), usage, actual_model, orchestrator_model),
        render_cost(matrix.get("benchmark", []), usage, int(static.get("repo_profile", {}).get("est_tokens", 0)), actual_model, orchestrator_model),
        render_recommendations(matrix.get("recommendations", [])),
    ]

    return (
        "<!doctype html>\n<html lang=\"en\"><head>"
        "<meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<title>Agentic readiness · {e(name)}</title>"
        f"<script>{THEME_BOOT_JS}</script>"
        f"<style>{CSS}</style></head><body>"
        f'<a class="skip-link" href="#scorecard">Skip to scorecard</a>'
        f'<div role="status" aria-live="polite" class="sr-only" id="live-region"></div>'
        f'<div class="app">'
        f'<nav class="side" aria-label="Sections">'
        f'<div class="brand"><span class="dot"></span><span>Readiness</span></div>'
        f'<h1>Sections</h1><ol>{nav_html}</ol></nav>'
        f'<main>{render_top_header(repo_path, ts, iso_ts)}{"".join(sections)}</main></div>'
        f"<script>{SCROLLSPY}</script></body></html>\n"
    )


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--static", required=True)
    ap.add_argument("--usage",  required=True)
    ap.add_argument("--matrix", required=True)
    ap.add_argument("--out",    default="./.repo-audit.html")
    args = ap.parse_args(argv)

    static = json.loads(Path(args.static).read_text(encoding="utf-8"))
    usage  = json.loads(Path(args.usage).read_text(encoding="utf-8"))
    matrix = json.loads(Path(args.matrix).read_text(encoding="utf-8"))

    sessions = usage.get("sessions") or {}
    missing = sum(1 for v in sessions.values() if v.get("status") != "ok")
    warn_count = len(matrix.get("warnings") or []) + (1 if missing else 0)

    out_path = Path(args.out)
    out_path.write_text(build_html(static, usage, matrix), encoding="utf-8")
    print(out_path)
    print(f"warnings={warn_count}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
