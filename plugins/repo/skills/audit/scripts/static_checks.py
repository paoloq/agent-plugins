#!/usr/bin/env python3
"""Static repo checks for the agentic-readiness command.

Stdlib-only. Walks a repo and emits a JSON report on stdout with deterministic
top-level keys:

    {
      "repo_profile":       {...},   # size, file counts, heaviest paths
      "agent_instructions": {...},   # CLAUDE.md / AGENTS.md presence + shape
      "tests":              {...},   # detected test/lint/typecheck setup
      "hygiene":            {...}    # secrets, big binaries, gitignore signals
    }

Usage:
    python3 static_checks.py <repo_path> [--out PATH]

Token counts are heuristic (chars/4) — the LLM orchestrator reconciles them
with `ccusage` data for accurate cost figures.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Where to look for agent-instruction files. Top-of-tree is the most common
# layout, but dotfiles/templating repos (like this one) ship the files under
# `instructions/{claude,codex}/` — without checking deeper we'd incorrectly
# flag those repos as `missing-both`.
INSTRUCTION_CANDIDATES = {
    "claude_md": [
        Path("CLAUDE.md"),
        Path("instructions/claude/CLAUDE.md"),
        Path(".claude/CLAUDE.md"),
        Path("docs/CLAUDE.md"),
    ],
    "agents_md": [
        Path("AGENTS.md"),
        Path("instructions/codex/AGENTS.md"),
        Path(".codex/AGENTS.md"),
        Path("docs/AGENTS.md"),
    ],
}

CHARS_PER_TOKEN = 4
MAX_HEAVIEST = 15
BIG_BINARY_BYTES = 1 * 1024 * 1024  # 1 MB

# Provider-recommended line budgets for the per-tool instruction file.
# Anthropic best-practices guidance puts the soft ceiling for CLAUDE.md at
# ~200 lines (longer files degrade adherence); OpenAI's AGENTS.md guidance
# puts the ceiling at ~150 lines. Exceeding these flips the file to `warn`.
INSTRUCTION_LINE_LIMITS = {
    "claude_md": 200,
    "agents_md": 150,
}

# Tokens that indicate the instruction file actually documents operational
# commands (test / build / lint / typecheck / run). OpenAI's Codex docs
# explicitly say Codex is trained to run commands referenced in AGENTS.md;
# Anthropic and Google make the same point for CLAUDE.md / GEMINI.md. We
# scan code-fence / inline-code segments only, to avoid prose false hits.
INSTRUCTION_COMMAND_RE = re.compile(
    r"`[^`\n]*\b(test|tests|build|lint|typecheck|type-check|run|make|pytest|npm|pnpm|yarn|cargo|go test|mix|gradle|mvn)\b[^`\n]*`",
    re.IGNORECASE,
)

TEXT_SUFFIXES = {
    ".md", ".markdown", ".rst", ".txt",
    ".py", ".pyi", ".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx",
    ".go", ".rs", ".java", ".kt", ".kts", ".scala", ".rb", ".php",
    ".c", ".h", ".cc", ".cpp", ".hpp", ".cs", ".swift", ".m", ".mm",
    ".sh", ".bash", ".zsh", ".fish",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg",
    ".html", ".css", ".scss", ".sass", ".less",
    ".sql", ".graphql", ".proto",
}

EXCLUDE_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__",
                "dist", "build", "target", ".next", ".nuxt", ".cache",
                ".pytest_cache", ".mypy_cache", ".tox", "vendor"}

# Files that are necessary, not optional — they're large by design (full
# resolved dependency trees) and must not be flagged as bloat. They still
# count toward `tracked_files` / `text_bytes` / `est_tokens` because they do
# contribute to read-the-whole-repo cost, but they're excluded from
# `heaviest_paths` / `heaviest_dirs` so the report doesn't surface them as
# "huge files" candidates for removal.
LOCKFILE_BASENAMES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb",
    "npm-shrinkwrap.json",
    "Cargo.lock",
    "go.sum",
    "gradle.lockfile", "packages.lock.json",
    "Gemfile.lock",
    "composer.lock",
    "Package.resolved",
    "poetry.lock", "uv.lock", "pdm.lock", "Pipfile.lock",
    "mix.lock", "conan.lock",
}


def _resolve_out_against_repo_root(raw: str) -> Path:
    """Resolve --out against the git toplevel; anchor relative paths to the
    user's repo even when cwd is a `.claude/worktrees/agent-*` subdir."""
    p = Path(raw)
    if p.is_absolute():
        return p
    try:
        top = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"], stderr=subprocess.DEVNULL, text=True
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return p.resolve()
    top_path = Path(top)
    parts = top_path.parts
    if ".claude" in parts and "worktrees" in parts:
        top_path = Path(*parts[:parts.index(".claude")])
    return (top_path / p).resolve()

SECRET_PATTERNS = [
    (re.compile(r"AKIA[0-9A-Z]{16}"), "aws-access-key"),
    (re.compile(r"-----BEGIN (RSA |OPENSSH |DSA |EC |PGP )?PRIVATE KEY-----"), "private-key"),
    (re.compile(r"\bxox[abprs]-[0-9A-Za-z-]{10,}"), "slack-token"),
    (re.compile(r"(?<![A-Za-z0-9_-])sk-ant-[A-Za-z0-9_-]{20,}"), "anthropic-api-key"),
    (re.compile(r"\bghp_[A-Za-z0-9]{36}\b"), "github-token"),
]

# Secret scanning is restricted to config-like surfaces. Source files routinely
# mention key prefixes for parsing/testing (e.g. `auth_tests.rs` parsing PEM
# blocks); flagging them as secret hits is pure noise. Real leaked secrets land
# in env files, configs, and notebooks — those are what we scan.
SECRET_SCAN_SUFFIXES = {
    ".env", ".envrc", ".cfg", ".conf", ".ini", ".properties",
    ".json", ".jsonc", ".yaml", ".yml", ".toml",
    ".txt", ".md", ".markdown",
}
SECRET_SCAN_BASENAMES = {".env", ".env.local", ".env.development", ".env.production"}

TEST_SIGNALS = {
    "pytest":   ["pytest.ini", "pyproject.toml", "tox.ini", "conftest.py"],
    "unittest": ["tests/", "test/"],
    "jest":     ["jest.config.js", "jest.config.ts", "jest.config.cjs"],
    "vitest":   ["vitest.config.js", "vitest.config.ts"],
    "mocha":    [".mocharc.js", ".mocharc.json", ".mocharc.yml"],
    "go":       ["go.mod"],
    "cargo":    ["Cargo.toml"],
    "rspec":    [".rspec", "spec/"],
}

LINT_SIGNALS = {
    "ruff":       ["ruff.toml", ".ruff.toml"],
    "flake8":     [".flake8", "setup.cfg"],
    "eslint":     [".eslintrc", ".eslintrc.js", ".eslintrc.json", ".eslintrc.cjs", "eslint.config.js"],
    "biome":      ["biome.json", "biome.jsonc"],
    "prettier":   [".prettierrc", ".prettierrc.json", ".prettierrc.js", "prettier.config.js"],
    "golangci":   [".golangci.yml", ".golangci.yaml", ".golangci.toml"],
    "rustfmt":    ["rustfmt.toml", ".rustfmt.toml"],
    "shellcheck": [".shellcheckrc"],
}

TYPECHECK_SIGNALS = {
    "pyright":    ["pyrightconfig.json"],
    "mypy":       ["mypy.ini", ".mypy.ini"],
    "tsconfig":   ["tsconfig.json"],
    "flow":       [".flowconfig"],
}

# Tools that ship their config inside a shared manifest (pyproject.toml,
# package.json) instead of a dedicated dotfile. Substring-matched against the
# manifest text, lowercased. Walked across the whole tree so monorepos with
# nested service folders (e.g. `services/api/pyproject.toml`) are picked up.
PYPROJECT_LINT_TOKENS = {
    "ruff":     ["[tool.ruff"],
    "flake8":   ["[tool.flake8", "[flake8]"],
    "black":    ["[tool.black"],
    "isort":    ["[tool.isort"],
    "pylint":   ["[tool.pylint"],
}
PYPROJECT_TYPECHECK_TOKENS = {
    "mypy":     ["[tool.mypy", "[mypy]"],
    "pyright":  ["[tool.pyright"],
    "ty":       ["[tool.ty]", "[tool.ty."],
    "pyre":     ["[tool.pyre"],
    "pytype":   ["[tool.pytype"],
}
PACKAGE_JSON_LINT_TOKENS = {
    "eslint":   ['"eslint"', '"eslintconfig"', '"@eslint/'],
    "prettier": ['"prettier"'],
    "biome":    ['"@biomejs/biome"'],
}
PACKAGE_JSON_TYPECHECK_TOKENS = {
    "tsc":      ['"typescript"'],
    "flow":     ['"flow-bin"'],
}

PYPROJECT_MANIFEST_NAMES = ("pyproject.toml", "setup.cfg", "tox.ini")
PACKAGE_JSON_MANIFEST_NAMES = ("package.json",)

# Pillar coverage. Each table is a presence probe — the point is "does the
# repo show evidence of this practice", not deep analysis. Signal breadth is
# modelled on Kodus's agent-readiness (the OSS reference) and Factory.ai's
# public pillar list. Signals derived from file *contents* (CI-integration
# scan, observability dep-manifest scan) are added by build_report below.
DEV_ENV_SIGNALS = {
    "devcontainer":     [".devcontainer/", ".devcontainer.json"],
    "docker":           ["Dockerfile", "docker-compose.yml", "compose.yml"],
    "make":             ["Makefile", "makefile", "GNUmakefile"],
    "asdf":             [".tool-versions"],
    "mise":             ["mise.toml", ".mise.toml"],
    "nvm":              [".nvmrc"],
    "pyenv":            [".python-version"],
    "rbenv":            [".ruby-version"],
    "phpenv":           [".php-version"],
    "swift-version":    [".swift-version"],
    "global-json":      ["global.json"],
    "rust-toolchain":   ["rust-toolchain.toml", "rust-toolchain"],
    "nix":              ["flake.nix", "shell.nix", "default.nix"],
    "vscode-tasks":     [".vscode/tasks.json", ".vscode/launch.json"],
    "lockfile-npm":     ["package-lock.json"],
    "lockfile-yarn":    ["yarn.lock"],
    "lockfile-pnpm":    ["pnpm-lock.yaml"],
    "lockfile-bun":     ["bun.lockb"],
    "lockfile-go":      ["go.sum"],
    "lockfile-cargo":   ["Cargo.lock"],
    "lockfile-gradle":  ["gradle.lockfile"],
    "lockfile-nuget":   ["packages.lock.json"],
    "lockfile-ruby":    ["Gemfile.lock"],
    "lockfile-composer":["composer.lock"],
    "lockfile-swift":   ["Package.resolved"],
    "lockfile-poetry":  ["poetry.lock"],
    "lockfile-uv":      ["uv.lock"],
    "lockfile-pdm":     ["pdm.lock"],
    "env-template":     [".env.example", ".env.template", ".env.sample"],
    "wrapper-maven":    ["mvnw"],
    "wrapper-gradle":   ["gradlew"],
    "setup-script":     ["script/setup", "script/bootstrap", "bin/setup", "scripts/setup.sh"],
}

OBSERVABILITY_SIGNALS = {
    "opentelemetry-config": ["otel-config.yaml", "otel-collector-config.yaml"],
    "prometheus":           ["prometheus.yml", "prometheus.yaml"],
    "grafana":              ["grafana/", "dashboards/"],
    "sentry":               ["sentry.properties", ".sentryclirc"],
    "datadog":              ["datadog.yaml", ".datadog.yaml"],
    "logging-config":       ["logging.conf", "log4j.properties", "log4j2.xml", "logback.xml"],
}

# Dependency-manifest tokens for observability/telemetry SDKs. A presence
# probe inside the manifests catches cloud-native repos that don't ship a
# config file alongside the SDK.
OBSERVABILITY_DEP_TOKENS = {
    "opentelemetry-sdk": ["opentelemetry"],
    "sentry-sdk":        ["@sentry/", "sentry-sdk", "sentry-go", "sentry-ruby", "raven-"],
    "datadog-sdk":       ["dd-trace", "ddtrace", "datadog-api-client", "datadogpy"],
    "prometheus-sdk":    ["prometheus_client", "prom-client", "prometheus-client", "micrometer"],
    "newrelic-sdk":      ["newrelic", "new-relic"],
    "elastic-apm-sdk":   ["elastic-apm", "elasticapm"],
}

OBSERVABILITY_DEP_FILES = [
    "package.json", "pyproject.toml", "requirements.txt", "Pipfile",
    "poetry.lock", "uv.lock", "Cargo.toml", "go.mod", "Gemfile",
    "composer.json", "build.gradle", "build.gradle.kts", "pom.xml",
]

SECURITY_SIGNALS = {
    "security-md":     ["SECURITY.md", "docs/SECURITY.md", ".github/SECURITY.md"],
    "codeowners":      ["CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS"],
    "dependabot":      [".github/dependabot.yml", ".github/dependabot.yaml"],
    "renovate":        ["renovate.json", ".github/renovate.json", "renovate.json5"],
    "pre-commit":      [".pre-commit-config.yaml", ".pre-commit-config.yml"],
    "gitleaks-config": [".gitleaks.toml", ".gitleaks.yaml"],
    "trivy-config":    [".trivyignore", "trivy.yaml"],
    "license":         ["LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING"],
}

# Substring-keyed scanner integrations found inside CI / pre-commit configs.
# The key is the canonical signal label; the values are case-insensitive
# substrings that, if present in any CI file, signal the integration. This
# catches the case where a scanner isn't installed as a top-level config but
# is wired into CI (e.g. `codeql-action` in a GitHub workflow).
SECURITY_CI_TOKENS = {
    "codeql":         ["codeql-action", "github/codeql"],
    "snyk":           ["snyk/actions", "snyk-action", "snyk.io"],
    "semgrep":        ["returntocorp/semgrep", "semgrep-action", "semgrep ci"],
    "sonarqube":      ["sonarsource/", "sonar-scanner", "sonarcloud"],
    "trivy":          ["aquasecurity/trivy", "trivy-action"],
    "gitleaks":       ["gitleaks/gitleaks", "gitleaks-action", "zricethezav/gitleaks"],
    "detect-secrets": ["yelp/detect-secrets", "detect-secrets"],
    "bandit":         ["pycqa/bandit", "bandit -r", "bandit-action"],
    "gosec":          ["securego/gosec", "gosec "],
    "brakeman":       ["presidentbeef/brakeman", "brakeman "],
    "pip-audit":      ["pypa/gh-action-pip-audit", "pip-audit"],
    "cargo-audit":    ["rustsec/audit-check", "cargo audit"],
    "dep-audit":      ["npm audit", "yarn audit", "pnpm audit"],
}

SECURITY_CI_FILES_GLOB = [".github/workflows/*.yml", ".github/workflows/*.yaml"]
SECURITY_CI_FILES_FIXED = [
    ".pre-commit-config.yaml", ".pre-commit-config.yml",
    ".gitlab-ci.yml", "azure-pipelines.yml", ".circleci/config.yml",
]


@dataclass
class RepoProfile:
    tracked_files: int = 0
    text_files: int = 0
    text_bytes: int = 0
    est_tokens: int = 0
    heaviest_paths: list[dict] = field(default_factory=list)
    heaviest_dirs: list[dict] = field(default_factory=list)


@dataclass
class AgentInstructions:
    claude_md: dict | None = None
    agents_md: dict | None = None
    mirror_parity: str = "n/a"
    at_least_one_present: bool = False


@dataclass
class Tests:
    runners: list[str] = field(default_factory=list)
    linters: list[str] = field(default_factory=list)
    typecheckers: list[str] = field(default_factory=list)
    ci_configs: list[str] = field(default_factory=list)
    coverage_tool: dict | None = None
    source_test_mapping: dict = field(default_factory=dict)


@dataclass
class Hygiene:
    gitignore_present: bool = False
    secret_hits: list[dict] = field(default_factory=list)
    big_binaries: list[dict] = field(default_factory=list)


@dataclass
class DevEnv:
    signals: list[str] = field(default_factory=list)


@dataclass
class Observability:
    signals: list[str] = field(default_factory=list)


@dataclass
class Security:
    signals: list[str] = field(default_factory=list)


@dataclass
class RepoShape:
    shape: str = "code"
    signals: dict = field(default_factory=dict)


@dataclass
class Evals:
    has_dir: bool = False
    files: list[dict] = field(default_factory=list)
    n_files: int = 0
    n_total_cases: int = 0
    coverage: list[dict] = field(default_factory=list)
    uncovered_items: list[str] = field(default_factory=list)
    quality_issues: list[dict] = field(default_factory=list)


@dataclass
class SkillQuality:
    n_skills: int = 0
    skills: list[dict] = field(default_factory=list)
    skills_missing_description: list[str] = field(default_factory=list)
    skills_over_line_limit: list[str] = field(default_factory=list)


@dataclass
class PromptHygiene:
    n_md_files: int = 0
    total_lines: int = 0
    oversized: list[dict] = field(default_factory=list)


@dataclass
class AgentConfig:
    # CLAUDE.md / AGENTS.md found anywhere below the root (excluding the root
    # files already surfaced under `agent_instructions`). Layered context is
    # the article's #1 recommendation for large codebases.
    nested_instructions: list[dict] = field(default_factory=list)
    nested_with_commands: int = 0
    # `.claude/settings.json` presence + permission rule counts. Deny rules
    # are how the article tells teams to exclude noise (node_modules, dist…)
    # from agent context so it doesn't bloat reads.
    has_settings_json: bool = False
    deny_rules: int = 0
    allow_rules: int = 0
    # Hook files under `.claude/hooks/`. The article specifically calls out
    # Stop / SessionStart hooks for self-improving CLAUDE.md and dynamic
    # context loading.
    hooks: list[str] = field(default_factory=list)
    # MCP servers declared in `.mcp.json` (root or `.claude/`).
    mcp_servers: list[str] = field(default_factory=list)
    # Codebase map for unconventional layouts.
    has_codebase_map: bool = False
    codebase_map_path: str | None = None
    # Age of the most-recently-touched root instruction file (git log).
    instructions_age_days: int | None = None
    # Convenience: how many configured "runtime affordances" exist.
    config_score: int = 0


@dataclass
class Walkability:
    root_dir_count: int = 0
    root_dirs: list[str] = field(default_factory=list)
    duplicate_name_pairs: list[list[str]] = field(default_factory=list)
    generated_dirs_present: list[str] = field(default_factory=list)
    prefix_collision_ratio: float = 0.0
    prefix_collision_token: str | None = None
    signals_score: int = 0


# Repo-shape detection. Agent-harness repos (plugin marketplaces, skill libs,
# prompt collections, eval suites) lack tests/dev-env/observability by design.
# Grading them against the code-repo pillar set gives a misleading D — instead
# we detect the shape and swap in Evals / Skill quality / Prompt hygiene.
SKILL_LINE_LIMIT = 200  # CLAUDE.md guidance: adherence degrades past ~200 lines.
MD_OVERSIZED_LINES = 300
HARNESS_SHAPE_THRESHOLD = 3  # ≥3 of 5 signals → agent-harness.
BUILD_MANIFEST_BASENAMES = {
    "package.json", "pyproject.toml", "Cargo.toml", "go.mod",
    "build.gradle", "build.gradle.kts", "pom.xml",
    "Gemfile", "composer.json", "setup.py", "setup.cfg",
}


def detect_repo_shape(repo: Path, files: list[Path], profile: RepoProfile) -> RepoShape:
    has_plugin_manifest = False
    has_skill_md = False
    for p in files:
        name = p.name
        if name == "plugin.json" and p.parent.name == ".claude-plugin":
            has_plugin_manifest = True
        elif name == "marketplace.json" and p.parent.name == ".claude-plugin":
            has_plugin_manifest = True
        elif name == "SKILL.md":
            has_skill_md = True
        if has_plugin_manifest and has_skill_md:
            break

    has_evals_dir = (repo / "evals").is_dir()

    md_bytes = sum(safe_size(p) for p in files if p.suffix.lower() in {".md", ".markdown"})
    markdown_dominant = profile.text_bytes > 0 and md_bytes / profile.text_bytes > 0.60

    no_top_build_manifest = not any(
        (repo / b).is_file() for b in BUILD_MANIFEST_BASENAMES
    )

    signals = {
        "has_plugin_manifest":   has_plugin_manifest,
        "has_skill_md":          has_skill_md,
        "has_evals_dir":         has_evals_dir,
        "markdown_dominant":     markdown_dominant,
        "no_top_build_manifest": no_top_build_manifest,
    }
    shape = "agent-harness" if sum(signals.values()) >= HARNESS_SHAPE_THRESHOLD else "code"
    return RepoShape(shape=shape, signals=signals)


def _enumerate_eval_targets(repo: Path) -> list[str]:
    """Return the list of plugin- and skill-level paths that ought to be
    covered by an eval. Per-plugin: `plugins/<name>`. Per-skill:
    `plugins/<name>/skills/<skill>`. Order: plugins first (sorted), then
    their skills (sorted)."""
    plugins_root = repo / "plugins"
    if not plugins_root.is_dir():
        return []
    targets: list[str] = []
    for plugin_dir in sorted(p for p in plugins_root.iterdir() if p.is_dir()):
        plugin_rel = f"plugins/{plugin_dir.name}"
        targets.append(plugin_rel)
        skills_root = plugin_dir / "skills"
        if not skills_root.is_dir():
            continue
        for skill_dir in sorted(s for s in skills_root.iterdir() if s.is_dir()):
            targets.append(f"{plugin_rel}/skills/{skill_dir.name}")
    return targets


def _eval_quality_probes(data: dict, cases_path: Path, repo: Path) -> dict:
    """Inspect a parsed cases.json for trigger / output coverage and resolve
    any referenced fixture paths. Fixtures are resolved against the plugin's
    virtual `<repo>/<item>/evals/` root when `item` is set, else against the
    cases.json's own directory."""
    triggers = data.get("triggers") if isinstance(data, dict) else None
    pos = neg = 0
    if isinstance(triggers, dict):
        pos = len(triggers.get("positive") or []) if isinstance(triggers.get("positive"), list) else 0
        neg = len(triggers.get("negative") or []) if isinstance(triggers.get("negative"), list) else 0
    outputs = data.get("output") or data.get("outputs") if isinstance(data, dict) else None
    n_output = len(outputs) if isinstance(outputs, list) else 0
    item_val = data.get("item") if isinstance(data, dict) else None
    if isinstance(item_val, str) and item_val.strip():
        base = (repo / item_val.strip().rstrip("/") / "evals").resolve()
    else:
        base = cases_path.parent
    fixtures_missing: list[str] = []
    if isinstance(outputs, list):
        for item in outputs:
            if not isinstance(item, dict):
                continue
            refs: list[str] = []
            for key in ("fixture", "fixtures"):
                v = item.get(key)
                if isinstance(v, str):
                    refs.append(v)
                elif isinstance(v, list):
                    refs.extend(x for x in v if isinstance(x, str))
            for ref in refs:
                resolved = (base / ref).resolve()
                if not resolved.exists():
                    fixtures_missing.append(ref)
    return {
        "has_triggers": isinstance(triggers, dict) and (pos > 0 or neg > 0),
        "n_positive": pos,
        "n_negative": neg,
        "has_output": n_output > 0,
        "n_output_assertions": n_output,
        "fixtures_missing": sorted(set(fixtures_missing)),
    }


def audit_evals(repo: Path) -> Evals:
    root = repo / "evals"
    if not root.is_dir():
        return Evals()
    files: list[dict] = []
    n_cases_total = 0
    covered_items: set[str] = set()
    quality_issues: list[dict] = []
    for p in root.rglob("*.json"):
        if any(part in EXCLUDE_DIRS for part in p.parts):
            continue
        n_cases = 0
        try:
            data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            data = None
        if isinstance(data, list):
            n_cases = len(data)
        elif isinstance(data, dict):
            for key in ("cases", "output", "outputs", "tests", "examples"):
                v = data.get(key)
                if isinstance(v, list):
                    n_cases = len(v)
                    break
        rel = str(p.relative_to(repo))
        probes: dict = {}
        item_ref: str | None = None
        if isinstance(data, dict):
            probes = _eval_quality_probes(data, p, repo)
            item_val = data.get("item")
            if isinstance(item_val, str) and item_val.strip():
                item_ref = item_val.strip().rstrip("/")
                covered_items.add(item_ref)
        files.append({
            "path": rel,
            "n_cases": n_cases,
            "item": item_ref,
            **probes,
        })
        n_cases_total += n_cases
        problems: list[str] = []
        if probes:
            if not probes.get("has_triggers"):
                problems.append("no-triggers")
            if not probes.get("has_output"):
                problems.append("no-output-assertions")
            if probes.get("fixtures_missing"):
                problems.append("fixtures-missing")
        if problems:
            quality_issues.append({"path": rel, "problems": problems})
    files.sort(key=lambda f: f["path"])
    targets = _enumerate_eval_targets(repo)
    coverage: list[dict] = []
    uncovered: list[str] = []
    for t in targets:
        # A target is covered by an eval whose `item` is the target itself,
        # the parent plugin (covers all its skills), or any descendant of
        # the target (a per-skill eval covers its parent plugin too).
        is_covered = False
        for c in covered_items:
            if c == t or t.startswith(c + "/") or c.startswith(t + "/"):
                is_covered = True
                break
        coverage.append({"item": t, "covered": is_covered})
        if not is_covered:
            uncovered.append(t)
    return Evals(
        has_dir=True,
        files=files,
        n_files=len(files),
        n_total_cases=n_cases_total,
        coverage=coverage,
        uncovered_items=uncovered,
        quality_issues=quality_issues,
    )


_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_FRONT_FIELD_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*)\s*:\s*(.*?)\s*$", re.MULTILINE)


def _parse_skill_frontmatter(text: str) -> dict[str, str] | None:
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return None
    fields: dict[str, str] = {}
    for fm in _FRONT_FIELD_RE.finditer(m.group(1)):
        fields[fm.group(1).lower()] = fm.group(2)
    return fields


def audit_skill_quality(repo: Path, files: list[Path]) -> SkillQuality:
    skills: list[dict] = []
    missing_desc: list[str] = []
    over_limit: list[str] = []
    for p in files:
        if p.name != "SKILL.md":
            continue
        rel = str(p.relative_to(repo))
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        line_count = text.count("\n") + (0 if text.endswith("\n") else 1)
        fm = _parse_skill_frontmatter(text)
        has_fm = fm is not None
        has_desc = bool(fm and fm.get("description"))
        is_over = line_count > SKILL_LINE_LIMIT
        skills.append({
            "path": rel,
            "has_frontmatter": has_fm,
            "has_description": has_desc,
            "line_count": line_count,
            "over_line_limit": is_over,
        })
        if not has_desc:
            missing_desc.append(rel)
        if is_over:
            over_limit.append(rel)
    skills.sort(key=lambda s: s["path"])
    return SkillQuality(
        n_skills=len(skills),
        skills=skills,
        skills_missing_description=sorted(missing_desc),
        skills_over_line_limit=sorted(over_limit),
    )


def audit_prompt_hygiene(repo: Path, files: list[Path]) -> PromptHygiene:
    n_files = 0
    total_lines = 0
    oversized: list[dict] = []
    for p in files:
        if p.suffix.lower() not in {".md", ".markdown"}:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        n_files += 1
        lines = text.count("\n") + (0 if text.endswith("\n") else 1)
        total_lines += lines
        if lines > MD_OVERSIZED_LINES:
            oversized.append({"path": str(p.relative_to(repo)), "lines": lines})
    oversized.sort(key=lambda o: -o["lines"])
    return PromptHygiene(n_md_files=n_files, total_lines=total_lines, oversized=oversized)


COVERAGE_CONFIG_FILES = {
    "coverage.py":  [".coveragerc", "pyproject.toml", "setup.cfg", "tox.ini"],
    "c8":           [".c8rc", ".c8rc.json", "package.json"],
    "nyc":          [".nycrc", ".nycrc.json", "package.json"],
    "jest":         ["jest.config.js", "jest.config.ts", "jest.config.cjs", "package.json"],
    "vitest":       ["vitest.config.js", "vitest.config.ts", "package.json"],
}

COVERAGE_TOOL_TOKENS = {
    "coverage.py":  ["[tool.coverage", "[coverage:", "pytest-cov", "--cov"],
    "c8":           ['"c8"', "c8 "],
    "nyc":          ['"nyc"', "nyc "],
    "jest":         ["--coverage", "collectCoverage", "coverageThreshold"],
    "vitest":       ["coverage:", "vitest run --coverage", "--coverage"],
}

COVERAGE_THRESHOLD_RE = re.compile(
    r"(?:fail[_-]under\s*=\s*|coverageThreshold[^}]*?(?:global|branches|lines|statements|functions)[^}]*?:\s*)(\d{1,3})",
    re.IGNORECASE | re.DOTALL,
)


def _walk_manifest_files(repo: Path, basenames: tuple[str, ...] | list[str]) -> list[Path]:
    """Return every file in `repo` whose basename is in `basenames`, walking
    nested directories with the same EXCLUDE_DIRS filter `_marker_present`
    uses. Monorepos commonly nest manifests under `services/*/pyproject.toml`
    or `packages/*/package.json`; a root-only scan misses those tools entirely.
    """
    wanted = set(basenames)
    out: list[Path] = []
    for dirpath, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS and not d.startswith(".")]
        for fname in files:
            if fname in wanted:
                out.append(Path(dirpath) / fname)
    return out


def _scan_files_for_tokens(files: list[Path], tokens_table: dict[str, list[str]]) -> set[str]:
    hits: set[str] = set()
    for p in files:
        low = _read_text_safe(p).lower()
        if not low:
            continue
        for label, tokens in tokens_table.items():
            if label in hits:
                continue
            if any(tok.lower() in low for tok in tokens):
                hits.add(label)
    return hits


def scan_manifest_lint_tokens(repo: Path) -> list[str]:
    py_files = _walk_manifest_files(repo, PYPROJECT_MANIFEST_NAMES)
    js_files = _walk_manifest_files(repo, PACKAGE_JSON_MANIFEST_NAMES)
    return sorted(
        _scan_files_for_tokens(py_files, PYPROJECT_LINT_TOKENS)
        | _scan_files_for_tokens(js_files, PACKAGE_JSON_LINT_TOKENS)
    )


def scan_manifest_typecheck_tokens(repo: Path) -> list[str]:
    py_files = _walk_manifest_files(repo, PYPROJECT_MANIFEST_NAMES)
    js_files = _walk_manifest_files(repo, PACKAGE_JSON_MANIFEST_NAMES)
    return sorted(
        _scan_files_for_tokens(py_files, PYPROJECT_TYPECHECK_TOKENS)
        | _scan_files_for_tokens(js_files, PACKAGE_JSON_TYPECHECK_TOKENS)
    )


def detect_coverage_tool(repo: Path) -> dict | None:
    """Best-effort detection of a coverage-tool configuration. Returns the
    first matching tool with the source file and any threshold we could parse;
    None if no signal. Walks nested manifests so monorepos with per-service
    `pyproject.toml` / `package.json` aren't missed. Substring match is
    deliberate — `pyproject.toml` may host coverage.py config under
    `[tool.coverage.*]` headers that a strict parser would have to
    special-case per language."""
    for tool, candidates in COVERAGE_CONFIG_FILES.items():
        tokens = COVERAGE_TOOL_TOKENS[tool]
        seen: list[Path] = []
        for fname in candidates:
            seen.extend(_walk_manifest_files(repo, (fname,)))
        for p in seen:
            text = _read_text_safe(p)
            if not text:
                continue
            if not any(tok.lower() in text.lower() for tok in tokens):
                continue
            threshold = None
            m = COVERAGE_THRESHOLD_RE.search(text)
            if m:
                try:
                    threshold = int(m.group(1))
                except ValueError:
                    threshold = None
            return {
                "tool": tool,
                "source": p.relative_to(repo).as_posix(),
                "threshold": threshold,
            }
    return None


TEST_DIR_NAMES = {"tests", "test", "__tests__", "spec", "specs"}
SOURCE_EXTS = {".py", ".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".go", ".rs", ".rb"}
TEST_FILE_PATTERNS = (
    re.compile(r"^test_(.+)\.py$"),
    re.compile(r"^(.+)_test\.py$"),
    re.compile(r"^(.+)\.test\.[a-z]+$"),
    re.compile(r"^(.+)\.spec\.[a-z]+$"),
    re.compile(r"^test_(.+)\.go$"),
    re.compile(r"^(.+)_test\.go$"),
    re.compile(r"^(.+)_spec\.rb$"),
)


def _is_in_test_dir(p: Path) -> bool:
    return any(part in TEST_DIR_NAMES for part in p.parts)


def _matches_test_filename(name: str) -> bool:
    return any(pat.match(name) for pat in TEST_FILE_PATTERNS)


def _is_in_source_dir(p: Path, repo: Path) -> bool:
    rel = p.relative_to(repo)
    if rel.parts[0].startswith("."):
        return False
    if rel.parts[0] in EXCLUDE_DIRS:
        return False
    if _is_in_test_dir(rel):
        return False
    # Colocated tests (`foo.test.ts` next to `foo.ts`) are common in JS/TS;
    # they must not be counted as source modules in their own right.
    if _matches_test_filename(p.name):
        return False
    # Skip nested plugin/example trees that ship their own non-app code —
    # they'd dominate the ratio with files no test could plausibly cover.
    if "examples" in rel.parts or "fixtures" in rel.parts:
        return False
    return True


def _test_stems(files: list[Path]) -> set[str]:
    # Match by filename pattern regardless of directory: many ecosystems
    # (JS/TS, Go, Rust) colocate tests next to source, so the test-dir gate
    # would miss them entirely.
    stems: set[str] = set()
    for p in files:
        for pat in TEST_FILE_PATTERNS:
            m = pat.match(p.name)
            if m:
                stems.add(m.group(1).lower())
                break
    return stems


def audit_source_test_mapping(repo: Path, files: list[Path]) -> dict:
    """Pair source modules with detected test files by stem.

    Heuristic only: covers Python / JS / TS / Go / Ruby naming conventions.
    Reports `n_source`, `n_with_test`, `coverage_ratio`, and up to 20
    uncovered module paths so the report stays bounded."""
    stems = _test_stems(files)
    if not stems:
        return {"n_source": 0, "n_with_test": 0, "coverage_ratio": 0.0,
                "uncovered_modules": []}
    n_source = 0
    n_with_test = 0
    uncovered: list[str] = []
    for p in files:
        if p.suffix.lower() not in SOURCE_EXTS:
            continue
        if not _is_in_source_dir(p, repo):
            continue
        n_source += 1
        stem = p.stem.lower()
        if stem in stems:
            n_with_test += 1
        else:
            uncovered.append(str(p.relative_to(repo)))
    ratio = (n_with_test / n_source) if n_source else 0.0
    uncovered.sort()
    return {
        "n_source": n_source,
        "n_with_test": n_with_test,
        "coverage_ratio": round(ratio, 3),
        "uncovered_modules": uncovered[:20],
    }


def list_tracked(repo: Path) -> list[Path] | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "ls-files", "-z"],
            capture_output=True, check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return [repo / p for p in out.stdout.decode("utf-8", "replace").split("\0") if p]


def walk_files(repo: Path) -> list[Path]:
    out: list[Path] = []
    for root, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS and not d.startswith(".")]
        for name in files:
            out.append(Path(root) / name)
    return out


def collect_files(repo: Path) -> list[Path]:
    tracked = list_tracked(repo)
    if tracked is not None:
        return tracked
    return walk_files(repo)


def is_text_path(p: Path) -> bool:
    return p.suffix.lower() in TEXT_SUFFIXES


def safe_size(p: Path) -> int:
    try:
        return p.stat().st_size
    except OSError:
        return 0


def profile_repo(repo: Path, files: list[Path]) -> RepoProfile:
    prof = RepoProfile()
    prof.tracked_files = len(files)
    per_dir: dict[str, int] = {}
    # `paths` drives the heaviest_paths surface and intentionally excludes
    # lockfiles (necessary, not optional — see LOCKFILE_BASENAMES). Lockfiles
    # still count toward `text_bytes` / `est_tokens` because they do
    # contribute to read-the-whole-repo cost.
    paths: list[tuple[int, Path]] = []
    for f in files:
        size = safe_size(f)
        if is_text_path(f):
            prof.text_files += 1
            prof.text_bytes += size
            if f.name not in LOCKFILE_BASENAMES:
                paths.append((size, f))
                top = f.relative_to(repo).parts[0] if f != repo else "."
                per_dir[top] = per_dir.get(top, 0) + size
    prof.est_tokens = prof.text_bytes // CHARS_PER_TOKEN
    paths.sort(key=lambda t: t[0], reverse=True)
    prof.heaviest_paths = [
        {"path": p.relative_to(repo).as_posix(), "bytes": s, "est_tokens": s // CHARS_PER_TOKEN}
        for s, p in paths[:MAX_HEAVIEST]
    ]
    prof.heaviest_dirs = sorted(
        ({"dir": d, "bytes": b, "est_tokens": b // CHARS_PER_TOKEN} for d, b in per_dir.items()),
        key=lambda x: x["bytes"], reverse=True,
    )[:MAX_HEAVIEST]
    return prof


def inspect_instructions_file(p: Path, repo: Path, line_limit: int) -> dict | None:
    if not p.exists():
        return None
    text = p.read_text(encoding="utf-8", errors="replace")
    headings = [ln.strip() for ln in text.splitlines() if ln.startswith("#")]
    lines = text.count("\n") + 1
    try:
        rel = p.relative_to(repo).as_posix()
    except ValueError:
        rel = p.name
    return {
        "path": rel,
        "bytes": p.stat().st_size,
        "est_tokens": p.stat().st_size // CHARS_PER_TOKEN,
        "lines": lines,
        "line_limit": line_limit,
        "over_line_limit": lines > line_limit,
        "headings": len(headings),
        "mentions_gotchas": any(w in text.lower() for w in ("gotcha", "pitfall", "footgun", "caveat")),
        "mentions_commands": bool(INSTRUCTION_COMMAND_RE.search(text)),
    }


def find_instructions(repo: Path, key: str) -> dict | None:
    limit = INSTRUCTION_LINE_LIMITS[key]
    for rel in INSTRUCTION_CANDIDATES[key]:
        info = inspect_instructions_file(repo / rel, repo, limit)
        if info is not None:
            return info
    return None


def parity_status(claude: dict | None, agents: dict | None) -> str:
    """Return the mirror-parity status.

    Having *either* CLAUDE.md or AGENTS.md present is sufficient for the
    pillar — the absence of the other is informational, not a failure. The
    "agents-only" / "claude-only" statuses signal "one is present; mirroring
    the other is encouraged but not required."
    """
    if claude is None and agents is None:
        return "missing-both"
    if claude is None:
        return "agents-only"
    if agents is None:
        return "claude-only"
    delta = abs(claude["bytes"] - agents["bytes"])
    ref = max(claude["bytes"], agents["bytes"]) or 1
    if delta / ref < 0.10:
        return "in-sync"
    return "drift"


def _marker_present(repo: Path, marker: str) -> bool:
    """True if `marker` exists at repo root or any non-excluded subdirectory.

    Monorepos commonly nest manifests (`codex-rs/Cargo.toml`,
    `frontend/package.json`); root-only detection misses them entirely.
    """
    target = repo / marker
    if marker.endswith("/"):
        dirname = marker.rstrip("/")
        if target.is_dir():
            return True
        for entry in os.walk(repo):
            dirs = entry[1]
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS and not d.startswith(".")]
            if dirname in dirs:
                return True
        return False
    if target.exists():
        return True
    basename = Path(marker).name
    for entry in os.walk(repo):
        dirs = entry[1]
        files = entry[2]
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS and not d.startswith(".")]
        if basename in files:
            return True
    return False


def detect_signals(repo: Path, table: dict[str, list[str]]) -> list[str]:
    hits: list[str] = []
    for name, markers in table.items():
        for marker in markers:
            if _marker_present(repo, marker):
                hits.append(name); break
    return hits


def _read_text_safe(p: Path, limit: int = 256 * 1024) -> str:
    """Return up to `limit` bytes of decoded text. Used by the two
    content-scanning helpers below; sized to handle large lockfiles cheaply."""
    try:
        with p.open("rb") as fh:
            data = fh.read(limit)
        return data.decode("utf-8", "replace")
    except OSError:
        return ""


def scan_security_ci_tokens(repo: Path) -> list[str]:
    """Scan CI / pre-commit configs for known scanner integrations. Returns
    the canonical signal labels that matched, sorted. Substring match is
    deliberate — workflow YAML embeds action refs like `aquasecurity/trivy@v1`,
    which a structural parser would have to handle case-by-case."""
    files: list[Path] = []
    for pattern in SECURITY_CI_FILES_GLOB:
        files.extend((repo).glob(pattern))
    for name in SECURITY_CI_FILES_FIXED:
        p = repo / name
        if p.exists():
            files.append(p)
    if not files:
        return []
    hits: set[str] = set()
    for f in files:
        low = _read_text_safe(f).lower()
        if not low:
            continue
        for label, tokens in SECURITY_CI_TOKENS.items():
            if any(tok.lower() in low for tok in tokens):
                hits.add(f"ci-{label}")
    return sorted(hits)


def scan_observability_deps(repo: Path) -> list[str]:
    """Scan dependency manifests for telemetry/observability SDK names. The
    most reliable observability signal for cloud-native repos that ship no
    standalone config file alongside the SDK. Walks nested manifests so
    monorepo subservices are covered."""
    files = _walk_manifest_files(repo, tuple(OBSERVABILITY_DEP_FILES))
    return sorted(_scan_files_for_tokens(files, OBSERVABILITY_DEP_TOKENS))


def detect_ci(repo: Path) -> list[str]:
    out: list[str] = []
    gha = repo / ".github" / "workflows"
    if gha.is_dir() and any(gha.iterdir()):
        out.append("github-actions")
    for fname in ("circle.yml", ".circleci/config.yml", ".gitlab-ci.yml",
                  ".travis.yml", "azure-pipelines.yml", "Jenkinsfile"):
        if (repo / fname).exists():
            out.append(fname.split("/")[-1])
    return out


def _is_secret_scan_target(p: Path) -> bool:
    if p.name in SECRET_SCAN_BASENAMES:
        return True
    return p.suffix.lower() in SECRET_SCAN_SUFFIXES


def scan_secrets(repo: Path, files: list[Path]) -> list[dict]:
    hits: list[dict] = []
    for f in files:
        if not _is_secret_scan_target(f) or safe_size(f) > 2 * 1024 * 1024:
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for pat, label in SECRET_PATTERNS:
            if pat.search(text):
                hits.append({"path": f.relative_to(repo).as_posix(), "kind": label})
                break
        if len(hits) >= 20:
            break
    return hits


def find_big_binaries(repo: Path, files: list[Path]) -> list[dict]:
    out: list[dict] = []
    for f in files:
        if is_text_path(f):
            continue
        if f.name in LOCKFILE_BASENAMES:
            # Binary lockfiles (e.g. bun.lockb) are necessary, not bloat.
            continue
        size = safe_size(f)
        if size >= BIG_BINARY_BYTES:
            out.append({"path": f.relative_to(repo).as_posix(), "bytes": size})
    out.sort(key=lambda x: x["bytes"], reverse=True)
    return out[:MAX_HEAVIEST]


# ---- Walkability ---------------------------------------------------------

# Root-level dir names that are almost always generated/build/cache output.
# Their presence at the top level hurts walkability — they crowd the listing
# and force every reader to skim them before finding source.
_WALKABILITY_GENERATED_DIRS = {
    "cdk.out", "dist", "build", "out", "target", "node_modules",
    "__pycache__", ".next", ".nuxt", ".cache", ".turbo", "coverage",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", "vendor",
    ".gradle", ".idea", ".vscode",
}

# Stem normalization for duplicate-name detection. Maps morphological
# variations to a canonical token. The goal is to flag pairs like
# `utility` vs `utils` vs `util`, or `shared_utils` vs `shared` vs `common`.
_WALKABILITY_STEM_MAP = {
    # util / shared / common / lib variants all flag the same anti-pattern:
    # an unscoped "miscellaneous helpers" bucket. Bucket them together so a
    # repo that has both `utility/` and `shared_utils/` gets flagged.
    "util": "shared_util", "utils": "shared_util", "utility": "shared_util",
    "utilities": "shared_util", "helper": "shared_util", "helpers": "shared_util",
    "shared": "shared_util", "shared_utils": "shared_util",
    "shared_util": "shared_util", "common": "shared_util",
    "lib": "shared_util", "libs": "shared_util",
    "config": "config", "configs": "config", "configuration": "config",
    "config_files": "config", "settings": "config", "conf": "config",
    "test": "test", "tests": "test", "__tests__": "test", "spec": "test",
    "specs": "test",
    "script": "script", "scripts": "script", "bin": "script", "tools": "script",
    "doc": "doc", "docs": "doc", "documentation": "doc",
    "asset": "asset", "assets": "asset", "static": "asset", "public": "asset",
    "service": "service", "services": "service",
    "model": "model", "models": "model",
    "view": "view", "views": "view",
    "controller": "controller", "controllers": "controller",
    "handler": "handler", "handlers": "handler",
}


def _normalize_dir_stem(name: str) -> str:
    """Lowercase + strip trailing digits/version markers, then map through
    the stem table. Returns the original lowercase name if no mapping
    applies."""
    n = name.lower().lstrip(".")
    n = re.sub(r"[_-]?v?\d+$", "", n)
    return _WALKABILITY_STEM_MAP.get(n, n)


def audit_walkability(repo: Path, files: list[Path]) -> Walkability:
    """Surface signals that make a repo hard for an agent to walk:
    too many root dirs, duplicate-purpose dirs, generated dirs at root,
    and filename prefixes that act as visual noise."""
    try:
        root_entries = [p for p in repo.iterdir() if p.is_dir()]
    except OSError:
        return Walkability()

    visible_dirs: list[Path] = []
    generated: list[str] = []
    for d in root_entries:
        name = d.name
        if name.startswith(".") and name not in {".github", ".claude"}:
            # Hide most dotdirs; .github / .claude are signal, not noise.
            continue
        if name in _WALKABILITY_GENERATED_DIRS:
            generated.append(name)
            continue
        visible_dirs.append(d)

    root_dirs = sorted(d.name for d in visible_dirs)

    # Duplicate-name pairs: bucket by normalized stem and report any bucket
    # with ≥2 entries. The output is a list of [a, b] pairs from each bucket.
    buckets: dict[str, list[str]] = {}
    for name in root_dirs:
        stem = _normalize_dir_stem(name)
        buckets.setdefault(stem, []).append(name)
    duplicate_pairs: list[list[str]] = []
    for names in buckets.values():
        if len(names) >= 2:
            # Emit every unordered pair so the report can list them all.
            for i in range(len(names)):
                for j in range(i + 1, len(names)):
                    duplicate_pairs.append([names[i], names[j]])

    # Prefix collision: fraction of code files whose basename starts with
    # the most common leading underscore-separated token. A high ratio means
    # filenames carry redundant project-prefix noise (e.g. `complaion_*.py`).
    code_files = [f for f in files if f.suffix.lower() in {
        ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".rb",
    }]
    prefix_counts: dict[str, int] = {}
    for f in code_files:
        stem = f.stem
        if not stem or stem.startswith("_"):
            continue
        head = re.split(r"[_\-]", stem, maxsplit=1)[0].lower()
        if len(head) < 3:
            continue
        prefix_counts[head] = prefix_counts.get(head, 0) + 1
    top_prefix: str | None = None
    top_ratio = 0.0
    if code_files:
        top_prefix, top_count = max(prefix_counts.items(), key=lambda kv: kv[1], default=(None, 0))
        if top_prefix is not None:
            top_ratio = round(top_count / len(code_files), 3)
            if top_ratio < 0.4 or top_count < 5:
                top_prefix = None
                top_ratio = 0.0

    # Signals score (0–4): one point per concerning signal. Used by the
    # Walkability pillar grading rule in SKILL.md.
    score = 0
    if len(root_dirs) > 12:
        score += 1
    if duplicate_pairs:
        score += 1
    if generated:
        score += 1
    if top_prefix is not None:
        score += 1

    return Walkability(
        root_dir_count=len(root_dirs),
        root_dirs=root_dirs,
        duplicate_name_pairs=duplicate_pairs,
        generated_dirs_present=sorted(generated),
        prefix_collision_ratio=top_ratio,
        prefix_collision_token=top_prefix,
        signals_score=score,
    )


# ---- Agent runtime configuration -----------------------------------------

# Filenames that explicitly enumerate top-level directories — i.e. a codebase
# map that compensates for an otherwise hard-to-walk layout.
_CODEBASE_MAP_NAMES = {
    "ARCHITECTURE.md", "STRUCTURE.md", "MAP.md", "REPOSITORY.md",
    "CODEMAP.md", "LAYOUT.md", "ORGANIZATION.md",
}

# Skip these dirs when hunting for nested instruction files — they'd just
# surface vendored copies that don't reflect repo intent.
_AGENT_CONFIG_SKIP_DIRS = EXCLUDE_DIRS | {".claude", ".codex", "node_modules"}


def _walk_nested_instructions(repo: Path) -> list[dict]:
    """Find CLAUDE.md / AGENTS.md below the root. Excludes any path returned
    by `find_instructions` (those are the "root" surface)."""
    root_paths = set()
    for key in ("claude_md", "agents_md"):
        for rel in INSTRUCTION_CANDIDATES[key]:
            root_paths.add((repo / rel).resolve())
    found: list[dict] = []
    for dirpath, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in _AGENT_CONFIG_SKIP_DIRS]
        for fname in files:
            if fname not in {"CLAUDE.md", "AGENTS.md"}:
                continue
            p = (Path(dirpath) / fname).resolve()
            if p in root_paths:
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            lines = text.count("\n") + (0 if text.endswith("\n") else 1)
            found.append({
                "path": p.relative_to(repo).as_posix(),
                "lines": lines,
                "mentions_commands": bool(INSTRUCTION_COMMAND_RE.search(text)),
            })
    found.sort(key=lambda x: x["path"])
    return found


def _read_settings_json(repo: Path) -> tuple[bool, int, int]:
    p = repo / ".claude" / "settings.json"
    if not p.is_file():
        return False, 0, 0
    try:
        data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return True, 0, 0
    perms = data.get("permissions") if isinstance(data, dict) else None
    deny = allow = 0
    if isinstance(perms, dict):
        d = perms.get("deny")
        a = perms.get("allow")
        if isinstance(d, list):
            deny = len(d)
        if isinstance(a, list):
            allow = len(a)
    return True, deny, allow


def _list_hooks(repo: Path) -> list[str]:
    hooks_dir = repo / ".claude" / "hooks"
    if not hooks_dir.is_dir():
        return []
    out: list[str] = []
    for entry in sorted(hooks_dir.iterdir()):
        if entry.is_file():
            out.append(entry.name)
    return out


def _list_mcp_servers(repo: Path) -> list[str]:
    for rel in (".mcp.json", ".claude/.mcp.json"):
        p = repo / rel
        if not p.is_file():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            return []
        servers = data.get("mcpServers") if isinstance(data, dict) else None
        if isinstance(servers, dict):
            return sorted(servers.keys())
    return []


def _find_codebase_map(repo: Path) -> str | None:
    for name in _CODEBASE_MAP_NAMES:
        p = repo / name
        if p.is_file():
            return name
    # README that enumerates top-level dirs as bullets — a heuristic: the
    # README must mention at least 3 actual top-level dir names as ``code``
    # spans or in a list. Avoids matching prose READMEs.
    for name in ("README.md", "README.rst"):
        p = repo / name
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        try:
            top_dirs = {d.name for d in repo.iterdir()
                        if d.is_dir() and not d.name.startswith(".")}
        except OSError:
            return None
        if not top_dirs:
            return None
        hits = 0
        for d in top_dirs:
            # Match `dir/` or `dir` inside backticks or as a list item.
            if re.search(rf"`{re.escape(d)}/?`", text) or re.search(
                rf"^[\s>*-]+{re.escape(d)}/", text, re.MULTILINE
            ):
                hits += 1
                if hits >= 3:
                    return name
    return None


def _instructions_age_days(repo: Path, claude: dict | None, agents: dict | None) -> int | None:
    """Return age (days) of the *most-recently-modified* root instruction
    file as reported by git log. None if no git history or no file."""
    candidates = [info for info in (claude, agents) if info]
    if not candidates:
        return None
    youngest: int | None = None
    for info in candidates:
        path = info.get("path")
        if not isinstance(path, str):
            continue
        try:
            out = subprocess.check_output(
                ["git", "-C", str(repo), "log", "-1", "--format=%ct", "--", path],
                stderr=subprocess.DEVNULL, text=True,
            ).strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            return None
        if not out:
            continue
        try:
            import time as _time
            age = int((_time.time() - int(out)) // 86400)
        except (ValueError, OverflowError):
            continue
        youngest = age if youngest is None else min(youngest, age)
    return youngest


def audit_agent_config(repo: Path, claude: dict | None, agents: dict | None) -> AgentConfig:
    nested = _walk_nested_instructions(repo)
    has_settings, deny, allow = _read_settings_json(repo)
    hooks = _list_hooks(repo)
    mcp = _list_mcp_servers(repo)
    map_path = _find_codebase_map(repo)
    age = _instructions_age_days(repo, claude, agents)
    score = sum([
        1 if nested else 0,
        1 if has_settings and deny > 0 else 0,
        1 if hooks else 0,
        1 if mcp else 0,
        1 if map_path else 0,
    ])
    return AgentConfig(
        nested_instructions=nested,
        nested_with_commands=sum(1 for n in nested if n["mentions_commands"]),
        has_settings_json=has_settings,
        deny_rules=deny,
        allow_rules=allow,
        hooks=hooks,
        mcp_servers=mcp,
        has_codebase_map=map_path is not None,
        codebase_map_path=map_path,
        instructions_age_days=age,
        config_score=score,
    )


def build_report(repo: Path) -> dict:
    files = collect_files(repo)
    profile = profile_repo(repo, files)
    claude = find_instructions(repo, "claude_md")
    agents = find_instructions(repo, "agents_md")
    instructions = AgentInstructions(
        claude_md=claude,
        agents_md=agents,
        mirror_parity=parity_status(claude, agents),
        at_least_one_present=(claude is not None or agents is not None),
    )
    tests = Tests(
        runners=detect_signals(repo, TEST_SIGNALS),
        linters=sorted(set(detect_signals(repo, LINT_SIGNALS))
                       | set(scan_manifest_lint_tokens(repo))),
        typecheckers=sorted(set(detect_signals(repo, TYPECHECK_SIGNALS))
                            | set(scan_manifest_typecheck_tokens(repo))),
        ci_configs=detect_ci(repo),
        coverage_tool=detect_coverage_tool(repo),
        source_test_mapping=audit_source_test_mapping(repo, files),
    )
    hygiene = Hygiene(
        gitignore_present=(repo / ".gitignore").exists(),
        secret_hits=scan_secrets(repo, files),
        big_binaries=find_big_binaries(repo, files),
    )
    dev_env = DevEnv(signals=detect_signals(repo, DEV_ENV_SIGNALS))
    observability = Observability(
        signals=sorted(set(detect_signals(repo, OBSERVABILITY_SIGNALS))
                       | set(scan_observability_deps(repo))),
    )
    security = Security(
        signals=sorted(set(detect_signals(repo, SECURITY_SIGNALS))
                       | set(scan_security_ci_tokens(repo))),
    )
    repo_shape = detect_repo_shape(repo, files, profile)
    evals = audit_evals(repo)
    skill_quality = audit_skill_quality(repo, files)
    prompt_hygiene = audit_prompt_hygiene(repo, files)
    walkability = audit_walkability(repo, files)
    agent_config = audit_agent_config(repo, claude, agents)
    return {
        "repo_profile":       asdict(profile),
        "repo_shape":         asdict(repo_shape),
        "agent_instructions": asdict(instructions),
        "agent_config":       asdict(agent_config),
        "tests":              asdict(tests),
        "hygiene":            asdict(hygiene),
        "dev_env":            asdict(dev_env),
        "observability":      asdict(observability),
        "security":           asdict(security),
        "evals":              asdict(evals),
        "skill_quality":      asdict(skill_quality),
        "prompt_hygiene":     asdict(prompt_hygiene),
        "walkability":        asdict(walkability),
    }


def main(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("repo_path")
    ap.add_argument("--out", help="Write JSON to this path instead of stdout")
    args = ap.parse_args(argv[1:])
    repo = Path(args.repo_path).resolve()
    if not repo.is_dir():
        print(f"error: not a directory: {repo}", file=sys.stderr)
        return 2
    report = build_report(repo)
    if args.out:
        out_path = _resolve_out_against_repo_root(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(out_path)
    else:
        json.dump(report, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
