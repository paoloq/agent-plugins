#!/bin/bash
# PreToolUse hook: auto-approve specific bundled scripts of this plugin.
# Each script's absolute path is passed as an argument from hooks.json so the
# match is anchored to THIS plugin only — another plugin shipping a script with
# the same basename will not be approved by this hook.
# Every other Bash command falls through to the normal permission prompt.

set -u

input=$(cat)
command=$(printf '%s' "$input" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("tool_input", {}).get("command", ""), end="")' 2>/dev/null) || exit 0

matched=""
for script in "$@"; do
  case "$command" in
    "$script"|"$script "*)
      matched="$script"
      break
      ;;
    "python3 $script"|"python3 $script "*)
      matched="$script"
      break
      ;;
  esac
done

if [ -n "$matched" ]; then
  printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"allow","permissionDecisionReason":"bundled plugin script"}}\n'
fi

exit 0
