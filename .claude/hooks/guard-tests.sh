#!/usr/bin/env bash
# PreToolUse hook for Edit|Write — TEST INTEGRITY GUARD.
#
# Stops Claude from "passing" tests by weakening them instead of fixing the
# code. It inspects the proposed change and BLOCKS (exit 2) when the edit:
#   1. introduces a skip / only / xfail / disabled-test marker,
#   2. introduces a type/lint suppression (# type: ignore, @ts-ignore,
#      eslint-disable),
#   3. removes assertions from an existing test file, or
#   4. touches a coverage threshold / test-ignore setting in a config file.
#
# Writing NEW tests (which add assertions, not skips) passes cleanly.
# Legitimately deleting or skipping a test is still possible — you just do it
# yourself, outside Claude, so it's a deliberate human decision.
#
# Contract: exit 2 blocks the call and shows stderr to Claude as the reason.

set -uo pipefail
payload="$(cat)"

get() { # $1 = tool_input key
  if command -v jq >/dev/null 2>&1; then
    printf '%s' "$payload" | jq -r ".tool_input.$1 // empty"
  else
    printf '%s' "$payload" | python3 -c "import sys,json;print(json.load(sys.stdin).get('tool_input',{}).get('$1',''))"
  fi
}

file="$(get file_path)"
[ -z "${file:-}" ] && exit 0

new_string="$(get new_string)"
old_string="$(get old_string)"
content="$(get content)"

# ADDED = text being introduced; REMOVED = text being taken away.
# Edit -> new_string / old_string. Write -> content / (current file on disk).
if [ -n "$content" ]; then
  added="$content"
  if [ -f "$file" ]; then removed="$(cat "$file")"; else removed=""; fi
else
  added="$new_string"
  removed="$old_string"
fi
[ -z "$added$removed" ] && exit 0

count() { printf '%s' "$1" | grep -oiE "$2" 2>/dev/null | wc -l | tr -d ' '; }
block() {
  echo "BLOCKED by test-integrity guard: $1" >&2
  echo "Fix the CODE so the test passes — do not weaken the test. If this change" >&2
  echo "is genuinely intended, make it yourself outside Claude Code." >&2
  exit 2
}

is_test=0
case "$file" in
  *test_*.py|*_test.py|*/tests/*|*/test/*|\
  *.test.ts|*.test.tsx|*.test.js|*.test.jsx|\
  *.spec.ts|*.spec.tsx|*.spec.js|*.spec.jsx|*/__tests__/*) is_test=1 ;;
esac

is_cfg=0
case "$file" in
  */pyproject.toml|*/pytest.ini|*/setup.cfg|*/tox.ini|*/.coveragerc|\
  *jest.config.*|*vitest.config.*|*/conftest.py) is_cfg=1 ;;
esac

# --- Rule 1 & 2: net-new skip markers or suppressions (any file) -------------
# POSIX character classes only ([[:space:]], not \s/\b) so these work under
# BSD grep on macOS as well as GNU grep on Linux.
SKIP='@pytest\.mark\.skip|pytest\.skip\(|@unittest\.skip|skipif|@pytest\.mark\.xfail|xfail\(|describe\.skip|it\.skip|test\.skip|\.only\(|describe\.only|it\.only|test\.only|xdescribe\(|xit\(|fdescribe\(|fit\('
SUPP='#[[:space:]]*type:[[:space:]]*ignore|@ts-ignore|@ts-expect-error|eslint-disable|/\*[[:space:]]*istanbul ignore'

if [ "$(count "$added" "$SKIP")" -gt "$(count "$removed" "$SKIP")" ]; then
  block "adds a skip/only/xfail marker that disables a test"
fi
if [ "$(count "$added" "$SUPP")" -gt "$(count "$removed" "$SUPP")" ]; then
  block "adds a type/lint suppression to silence an error instead of fixing it"
fi

# --- Rule 3: assertion erosion in test files ---------------------------------
if [ "$is_test" -eq 1 ]; then
  ASSERT='assert|self\.assert|expect\(|\.toBe|\.toEqual|\.toThrow'
  if [ "$(count "$removed" "$ASSERT")" -gt "$(count "$added" "$ASSERT")" ]; then
    block "removes assertions from a test file (net loss of test coverage)"
  fi
fi

# --- Rule 4: coverage / ignore knobs in config files -------------------------
if [ "$is_cfg" -eq 1 ]; then
  KNOBS='fail_under|cov-fail-under|--no-cov|no_cov|testPathIgnorePatterns|coveragePathIgnorePatterns|collectCoverageFrom|--deselect'
  if [ "$(count "$added" "$KNOBS")" -gt 0 ]; then
    block "changes a coverage threshold or test-ignore setting — adjust these yourself so it's an explicit decision"
  fi
fi

exit 0
