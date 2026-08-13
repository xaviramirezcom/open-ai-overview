# own-overview — Project Memory

Loaded into Claude Code at the start of every session. Keep it short, specific,
and verifiable. Detailed conventions live in `.claude/rules/`; the full design is
`docs/architecture.md`.

## What this is

A runnable, multi-tenant **RAG reference over Guidewire Cloud Data Access (CDA)** —
"build your company's own AI Overview" over private InsuranceSuite data. It's a
teaching-grade reference implementation (bring your own data + keys), backed by a
6-post blog series (one post per pipeline stage).

- **Stack:** LangGraph + LangChain · AWS Bedrock (Claude + Titan) default ·
  OpenSearch · LocalStack + Terraform · Python 3.11 / uv / MIT. Every stage has a
  zero-cloud local fallback (`PROVIDER=local`, `hash` embedder, in-process store).

## Golden rules

1. **Tests are not optional.** New behavior ships with tests in the same change,
   and the suite stays green at ≥85% coverage. If you add behavior without a
   test, you are not done.
2. **Tests are a contract.** A failing test means the CODE is wrong. NEVER weaken
   a test to go green — no `@pytest.mark.skip`/`xfail`, no loosened/removed
   assertions, no `# type: ignore` to silence an error, no lowering the coverage
   floor. These are blocked by the `guard-tests` hook and by CI. If a change is
   genuinely warranted, ask the human to make it.
3. **Respect the dependency rule.** The core (`contracts`, `security`,
   `grounding`, `evals`) never imports adapters, `config`, or heavy libraries;
   pipeline nodes take components injected. Enforced by `import-linter` — a
   violation fails the build. See `.claude/rules/python.md`.
4. **Type everything.** No untyped public functions; `mypy own_overview` must
   pass. Stub heavy libs under `[[tool.mypy.overrides]]`, don't `# type: ignore`.
5. **Isolation & governance are the product.** Every query carries a
   `TenantScope` (tenant + env); retrieval filters on tenant/env/roles,
   fail-closed. CDA DELETE tombstones evict from the index. Never filter after
   generation.
6. **Keep secrets out of git.** Never read or write `.env*`; use `.env.example`
   placeholders. Never hardcode credentials or AWS keys.

## Commands (source of truth — keep these working)

```bash
uv pip install -e '.[local,dev]'   # install (add [evals] for RAGAS)
ruff check . && ruff format --check .   # lint + format
mypy own_overview                       # type check
lint-imports                            # architecture boundaries (.importlinter)
pytest --cov=own_overview --cov-report=term-missing --cov-fail-under=85
own-overview seed                       # generate + ingest synthetic CDA data
own-overview query "..." --role adjuster
```

Run the whole gate with `/verify`.

## Definition of done for any task

- [ ] `ruff check` + `ruff format --check` clean.
- [ ] `mypy own_overview` passes; `lint-imports` passes.
- [ ] New behavior covered by tests; full suite green at ≥85% coverage.
- [ ] No secrets, no debug prints left behind.
- [ ] README/architecture updated if public behavior changed.

## Architecture (ports & adapters)

`contracts.py` = domain types + component Protocols (the ports). Adapters
(`embeddings/`, `llm/`, `vectorstore/`, `retrieval/`) implement them, lazy-
importing heavy deps. `config.py` is the composition root (`build_*` factories);
`pipeline/graph.py` wires the LangGraph graph — **one node per phase**
(fan_out → retrieve → rerank → ground → guardrails → audit). Ingestion
(`ingestion/cda/`) reacts to CDA Lifecycle Events (not raw S3), merges CDC rows,
and evicts on tombstones. Full reference: `docs/architecture.md`.

## Guardrails (this repo enforces its own quality)

- **Claude Code hooks** (`.claude/settings.json` → `.claude/hooks/`): block
  dangerous shell commands, guard test integrity (pre-edit), auto-format
  (post-edit), and remind of the DoD (stop).
- **Pre-commit** (`.githooks/pre-commit`, enable with
  `git config core.hooksPath .githooks`): fast gate — ruff, format, lint-imports,
  pytest+coverage, and a staged-test skip-marker block.
- **CI** (`.github/workflows/ci.yml`): the authoritative full gate on every push.
