#!/usr/bin/env bash
# PreToolUse hook for Bash. Defense-in-depth beyond permissions.deny:
# inspects the actual command string and blocks destructive / unsafe patterns
# even when they're constructed in ways a permission glob might miss.
#
# Contract: exit 2 => block the tool call (stderr is shown to Claude as the
# reason). exit 0 => allow (normal permission flow continues).

set -euo pipefail

payload="$(cat)"

# Extract the command; prefer jq, fall back to python3.
if command -v jq >/dev/null 2>&1; then
  cmd="$(printf '%s' "$payload" | jq -r '.tool_input.command // ""')"
else
  cmd="$(printf '%s' "$payload" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("tool_input",{}).get("command",""))')"
fi

block() {
  echo "BLOCKED by guardrail: $1" >&2
  echo "If this was intentional, run it yourself outside Claude Code." >&2
  exit 2
}

# Destructive filesystem / git operations.
case "$cmd" in
  *"rm -rf"*|*"rm -fr"*)                block "recursive force delete (rm -rf)";;
  *":(){"*)                            block "fork bomb pattern";;
  *"git reset --hard"*)                block "git reset --hard discards work";;
  *"git clean -"*[fdx]*)               block "git clean would delete untracked files";;
  *"git push --force"*|*"git push -f"*) block "force push rewrites remote history";;
  *"chmod -R 777"*)                    block "world-writable permissions";;
  *" > /dev/sd"*|*"mkfs"*|*"dd if="*)  block "raw disk write";;
esac

# Secret exfiltration: piping env/secrets to the network.
if printf '%s' "$cmd" | grep -Eq '(env|cat .*\.env|printenv).*\|.*(curl|wget|nc)'; then
  block "possible secret exfiltration to the network"
fi

# Test-integrity: don't let the test/quality gates get bypassed from the shell.
# `--no-verify` is matched anywhere after `git commit`; `-n` only immediately
# after `commit` (so it can't false-positive on " -n " inside a commit message).
if printf '%s' "$cmd" | grep -Eq 'git[[:space:]]+commit[[:space:]]+-n([[:space:]]|$)' \
   || printf '%s' "$cmd" | grep -Eq 'git[[:space:]]+commit.*--no-verify'; then
  block "git commit -n/--no-verify skips the pre-commit test gate"
fi
case "$cmd" in
  *"pytest"*"--no-cov"*)   block "pytest --no-cov disables the coverage gate";;
  *"pytest"*"--deselect"*) block "pytest --deselect silently drops tests";;
esac

exit 0
