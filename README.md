# 🧩 cc-plugins

A plugin marketplace for [Claude Code](https://docs.claude.com/en/docs/claude-code) — shipped as a single git repo.

## 🚀 Install

```text
/plugin marketplace add paoloq/cc-plugins
/plugin install prompt@cc-plugins
```

Or from a local clone:

```text
/plugin marketplace add /absolute/path/to/cc-plugins
/plugin install prompt@cc-plugins
```

Skills are invoked as `/<plugin>:<skill>` — e.g. `/prompt:draft`, `/prompt:review`.

## 📦 Plugins

| Name | Skills | Description |
| --- | --- | --- |
| [✍️ prompt](plugins/prompt/) | `draft` · `guides` · `review` · `revise` | Draft, revise, and review prompts using curated guides from Anthropic, OpenAI, and Google. |

## 🗂️ Layout

```
cc-plugins/
  .claude-plugin/marketplace.json     # Marketplace catalog
  plugins/<name>/
    .claude-plugin/plugin.json        # Plugin manifest
    skills/<skill>/                   # One directory per skill
```

## 🧪 Evals

```
python3 evals/run.py all
```

To run them automatically before every commit (one-time, per clone):

```
git config core.hooksPath .githooks
```

Bypass with `git commit --no-verify` or `PRE_COMMIT_SKIP_EVALS=1`.

## 📄 License

MIT — see [LICENSE](LICENSE).
