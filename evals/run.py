#!/usr/bin/env python3
"""Discover and execute per-plugin evals under `evals/<plugin>/cases.json`.

Stdlib only. Python >=3.10.

Layout:
    evals/<plugin>/cases.json     # {"item": "plugins/<plugin>", "triggers": {...}, "output": [...]}
Fixture paths in cases.json are resolved relative to `<repo>/<item>/evals/` so
that `../skills/<x>/SKILL.md` reaches `plugins/<plugin>/skills/<x>/SKILL.md`.

Usage:
    python evals/run.py {trigger,lint,output,all} [--item plugins/<name>]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EVALS_ROOT = Path(__file__).resolve().parent


@dataclass
class Item:
    id: str                  # e.g. "plugins/task"
    cases_path: Path         # evals/<plugin>/cases.json
    plugin_dir: Path         # REPO_ROOT / item
    fixture_base: Path       # plugin_dir / "evals"  (virtual; matches `..` semantics)


@dataclass
class Result:
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    failures: list[str] = field(default_factory=list)

    def merge(self, other: "Result") -> None:
        self.passed += other.passed
        self.failed += other.failed
        self.skipped += other.skipped
        self.failures.extend(other.failures)

    @property
    def ok(self) -> bool:
        return self.failed == 0


def discover_items() -> list[Item]:
    items: list[Item] = []
    for cases_path in sorted(EVALS_ROOT.glob("*/cases.json")):
        data = json.loads(cases_path.read_text(encoding="utf-8"))
        item_id = data["item"]
        plugin_dir = REPO_ROOT / item_id
        items.append(Item(
            id=item_id,
            cases_path=cases_path,
            plugin_dir=plugin_dir,
            fixture_base=plugin_dir / "evals",
        ))
    return items


# ---- frontmatter --------------------------------------------------------

def parse_frontmatter(md: Path) -> dict | None:
    text = md.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---", 4)
    if end < 0:
        return None
    block = text[4:end]
    return _parse_simple_yaml(block)


_SCALAR_RE = re.compile(r'^([A-Za-z0-9_-]+):\s*(.*)$')


def _parse_simple_yaml(block: str) -> dict:
    out: dict = {}
    for raw in block.splitlines():
        line = raw.rstrip()
        if not line or line.startswith("#"):
            continue
        m = _SCALAR_RE.match(line)
        if not m:
            raise ValueError(f"unsupported frontmatter line: {raw!r}")
        key, value = m.group(1), m.group(2).strip()
        out[key] = _coerce_scalar(value)
    return out


def _coerce_scalar(value: str):
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_strip_quotes(p.strip()) for p in inner.split(",")]
    if value == "true":
        return True
    if value == "false":
        return False
    return _strip_quotes(value)


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


# ---- lint ---------------------------------------------------------------

REQUIRED_CASES_KEYS = ("item", "triggers")
REQUIRED_TRIGGER_KEYS = ("must_match_any", "positive", "negative")
REQUIRED_OUTPUT_KEYS = ("id", "kind")


def run_lint(items: list[Item]) -> Result:
    res = Result()
    for item in items:
        errs = _lint_cases(item)
        # Lint frontmatter for each SKILL.md under the plugin.
        for skill_md in sorted(item.plugin_dir.glob("skills/*/SKILL.md")):
            try:
                fm = parse_frontmatter(skill_md)
            except ValueError as exc:
                errs.append(f"{skill_md.relative_to(REPO_ROOT)}: frontmatter parse: {exc}")
                continue
            if fm is None:
                errs.append(f"{skill_md.relative_to(REPO_ROOT)}: missing frontmatter")
                continue
            for key in ("name", "description"):
                if key not in fm:
                    errs.append(f"{skill_md.relative_to(REPO_ROOT)}: frontmatter missing {key!r}")
        if errs:
            res.failed += 1
            res.failures.append(f"{item.id} [lint]:\n  - " + "\n  - ".join(errs))
        else:
            res.passed += 1
    return res


def _lint_cases(item: Item) -> list[str]:
    errs: list[str] = []
    try:
        data = json.loads(item.cases_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"cases.json: {exc}"]
    for key in REQUIRED_CASES_KEYS:
        if key not in data:
            errs.append(f"cases.json: missing top-level key {key!r}")
    triggers = data.get("triggers", {})
    for key in REQUIRED_TRIGGER_KEYS:
        if key not in triggers:
            errs.append(f"cases.json triggers: missing key {key!r}")
    if triggers.get("must_match_any") == []:
        errs.append("cases.json triggers.must_match_any: empty")
    for case in data.get("output", []):
        for key in REQUIRED_OUTPUT_KEYS:
            if key not in case:
                errs.append(f"output[{case.get('id','?')}]: missing key {key!r}")
        if case.get("kind") not in ("golden", "judge"):
            errs.append(f"output[{case.get('id','?')}]: invalid kind {case.get('kind')!r}")
    return errs


# ---- trigger ------------------------------------------------------------

def run_trigger(items: list[Item]) -> Result:
    res = Result()
    for item in items:
        data = json.loads(item.cases_path.read_text(encoding="utf-8"))
        triggers = data["triggers"]
        phrases = [p.lower() for p in triggers["must_match_any"]]

        for prompt in triggers["positive"]:
            if not any(p in prompt.lower() for p in phrases):
                res.failed += 1
                res.failures.append(
                    f"{item.id} [trigger.positive]: {prompt!r} matches no phrase in {phrases}"
                )
            else:
                res.passed += 1
        for prompt in triggers["negative"]:
            hit = next((p for p in phrases if p in prompt.lower()), None)
            if hit is not None:
                res.failed += 1
                res.failures.append(
                    f"{item.id} [trigger.negative]: {prompt!r} contains trigger phrase {hit!r}"
                )
            else:
                res.passed += 1
    return res


# ---- output -------------------------------------------------------------

def run_output(items: list[Item], judge: bool) -> Result:
    res = Result()
    for item in items:
        data = json.loads(item.cases_path.read_text(encoding="utf-8"))
        for case in data.get("output", []):
            if case["kind"] == "judge" and not judge:
                res.skipped += 1
                continue
            errs = _check_golden(item, case)
            if errs:
                res.failed += 1
                res.failures.append(
                    f"{item.id} [output:{case['id']}]:\n  - " + "\n  - ".join(errs)
                )
            else:
                res.passed += 1
    return res


def _check_golden(item: Item, case: dict) -> list[str]:
    errs: list[str] = []
    fixture_paths: list[Path] = []
    if "fixture" in case:
        fixture_paths.append((item.fixture_base / case["fixture"]).resolve())
    for f in case.get("fixtures", []):
        fixture_paths.append((item.fixture_base / f).resolve())
    if not fixture_paths:
        errs.append("no fixture/fixtures declared")
        return errs

    must_contain = case.get("must_contain", [])
    must_not_contain = case.get("must_not_contain", [])
    regex_must_match = case.get("regex_must_match")
    regex_must_not_match = case.get("regex_must_not_match")
    required_fm_keys = case.get("frontmatter_required_keys")

    for fp in fixture_paths:
        if not fp.exists():
            try:
                rel = fp.relative_to(REPO_ROOT)
            except ValueError:
                rel = fp
            errs.append(f"fixture missing: {rel}")
            continue
        text = fp.read_text(encoding="utf-8")
        rel = fp.relative_to(REPO_ROOT)
        for needle in must_contain:
            if needle not in text:
                errs.append(f"{rel}: missing required substring: {needle!r}")
        for needle in must_not_contain:
            if needle in text:
                errs.append(f"{rel}: forbidden substring present: {needle!r}")
        if regex_must_match and not re.search(regex_must_match, text):
            errs.append(f"{rel}: regex_must_match did not match: {regex_must_match!r}")
        if regex_must_not_match and re.search(regex_must_not_match, text):
            errs.append(f"{rel}: regex_must_not_match matched: {regex_must_not_match!r}")
        if required_fm_keys and fp.suffix == ".md":
            try:
                fm = parse_frontmatter(fp) or {}
            except ValueError as exc:
                errs.append(f"{rel}: frontmatter parse: {exc}")
                continue
            missing = [k for k in required_fm_keys if k not in fm]
            if missing:
                errs.append(f"{rel}: frontmatter missing keys: {missing}")
    return errs


# ---- entrypoint ---------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("kind", choices=["trigger", "lint", "output", "all"])
    ap.add_argument("--judge", action="store_true", help="enable LLM-judge cases (not implemented)")
    ap.add_argument("--item", help="restrict to a single item id, e.g. plugins/task")
    args = ap.parse_args()

    items = discover_items()
    if args.item:
        items = [i for i in items if i.id == args.item]
        if not items:
            print(f"no items match {args.item!r}", file=sys.stderr)
            return 2

    total = Result()
    kinds = ["lint", "trigger", "output"] if args.kind == "all" else [args.kind]
    for kind in kinds:
        if kind == "lint":
            r = run_lint(items)
        elif kind == "trigger":
            r = run_trigger(items)
        else:
            r = run_output(items, judge=args.judge)
        print(f"[{kind}] passed={r.passed} failed={r.failed} skipped={r.skipped}")
        total.merge(r)

    if total.failures:
        print("\nfailures:")
        for f in total.failures:
            print(f"  - {f}")
    return 0 if total.ok else 1


if __name__ == "__main__":
    sys.exit(main())
