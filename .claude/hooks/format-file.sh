#!/usr/bin/env bash
# PostToolUse hook for Edit|Write. Auto-formats the file that was just written
# so every change lands already-formatted and diffs stay clean. This is an
# observation hook: it never blocks, and it silently no-ops if the relevant
# formatter isn't installed yet (e.g. before deps exist).

set -uo pipefail

payload="$(cat)"

if command -v jq >/dev/null 2>&1; then
  file="$(printf '%s' "$payload" | jq -r '.tool_input.file_path // empty')"
else
  file="$(printf '%s' "$payload" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("tool_input",{}).get("file_path",""))')"
fi

[ -z "${file:-}" ] && exit 0
[ -f "$file" ] || exit 0

case "$file" in
  *.py)
    if command -v ruff >/dev/null 2>&1; then
      ruff format "$file" >/dev/null 2>&1 || true
      ruff check --fix "$file" >/dev/null 2>&1 || true
    fi
    ;;
  *.ts|*.tsx|*.js|*.jsx|*.json|*.css|*.scss|*.md|*.mjs|*.cjs)
    if command -v npx >/dev/null 2>&1; then
      npx --no-install prettier --write "$file" >/dev/null 2>&1 || true
    fi
    ;;
esac

exit 0
