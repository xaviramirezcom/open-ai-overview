#!/usr/bin/env bash
# Stop hook. Runs when Claude finishes a turn. Advisory only (always exit 0) so
# it can never trap the session in a loop. Reminds Claude of the
# definition-of-done whenever Python source changed.

set -uo pipefail

if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  changed="$(git status --porcelain 2>/dev/null | grep -E '\.py$' || true)"
  if [ -n "$changed" ]; then
    {
      echo "── quality gate reminder ─────────────────────────────"
      echo "Python files changed. Before considering this done, confirm:"
      echo "  ruff check . && ruff format --check ."
      echo "  mypy own_overview && lint-imports"
      echo "  pytest --cov=own_overview --cov-fail-under=85"
      echo "  no secrets / debug prints left behind"
      echo "──────────────────────────────────────────────────────"
    } >&2
  fi
fi
exit 0
