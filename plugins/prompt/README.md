# ✍️ prompt

Four prompt-engineering skills backed by curated guidance from **Anthropic**, **OpenAI**, and **Google** — bundled verbatim, zero network calls.

| Skill | What it does |
| --- | --- |
| 📝 `/prompt:draft` | Draft a new prompt from intent. |
| 📚 `/prompt:guides` | Browse the bundled provider guides. |
| 🔍 `/prompt:review` | Score a prompt and emit a self-contained HTML report. |
| 🛠️ `/prompt:revise` | Rewrite a prompt against the guides. |

## 🚀 Install

```text
/plugin marketplace add paoloq/cc-plugins
/plugin install prompt@cc-plugins
```

## 📚 Guides

One file per model family, shipped verbatim under `guides/`:

- 🟢 `openai-gpt-5-5.md`
- 🟢 `openai-gpt-5-4.md`
- 🟣 `anthropic-claude-4.md`
- 🔵 `google-gemini-3.md`

Each file starts with HTML comment markers (`<!-- guide: <id> -->`, plus `provider`, `models`, `title`, `url`) so skills read them directly — no `Grep`, no Bash.

## 🗂️ Layout

```
plugins/prompt/
  .claude-plugin/plugin.json
  guides/                             # Upstream guides, verbatim
  references/paths.md                 # Shared cache-path convention
  skills/
    draft/SKILL.md
    guides/SKILL.md
    review/SKILL.md
      scripts/render_report.py        # JSON → HTML
      scripts/assets/                 # report.css, report.js, theme-boot.js
    revise/SKILL.md
```

Eval cases live at `evals/prompt/cases.json` in the repo root — outside plugin source so they aren't installed.

## 💾 Cache

Runs land under `~/.claude/cache/prompt/{review,revise}/<project>/<stamp>/`. Full convention in [`references/paths.md`](references/paths.md). Delete subtrees freely to reclaim space — nothing else references them.

## 🔗 Source

Skill content is maintained at [`dotagents/commands/prompt`](https://github.com/paoloqaz/dotagents/tree/main/commands/prompt). Path references and platform-specific UI mentions are runtime-agnostic.
