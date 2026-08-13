---
name: verify
description: Run the full quality gate and report what fails. Fix nothing here.
disable-model-invocation: false
---

# Full verification

Run the complete quality gate and report results concisely. Fix nothing here —
just run and summarize what passes and what fails.

From the repo root (activate `.venv` or prefix with `uv run`):

- `ruff format --check .`
- `ruff check .`
- `mypy own_overview`
- `lint-imports`  (dependency rule — see `.importlinter`)
- `pytest --cov=own_overview --cov-report=term-missing --cov-fail-under=85`

Report a short pass/fail table and the first actionable error for anything red.
