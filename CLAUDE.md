# CLAUDE.md

Piotroski F-scores for one EU country (Denmark, ADR-002) from ESEF filings, on Databricks Free Edition. Source of truth: `docs/PLAN.md` and `docs/adr/`. MVP done (v0.1.1). Current: Sprint v0.2, Nov 11–17, 2026 (prices, CDC/SCD2, dbt, orchestration).

## House rules

- Never commit data or secrets: no downloaded filings, API responses, tokens, `.databrickscfg`, workspace IDs or user emails. No employer, client or job-application names anywhere in the repo, commits or PRs (public repo). Test fixtures are hand-written and tiny.
- Conventional commits (`feat(silver): map current assets concepts`). One logical change per commit, one day's theme per PR, squash-merge to `main`. Never commit to `main` directly.
- Python 3.12 (matches the serverless environment). Use `uv run` for everything; ruff for lint and format.
- Business logic lives in `src/piotroski/` as `DataFrame -> DataFrame` functions. Pipeline files only wire them. Notebooks are scratch space and are never imported.
- Missing input means a null signal, never 0.
- Extraction runs outside Databricks (ADR-001) and must be idempotent.
- Ask before writing more than ~30 lines of code.
- Explain any API you suggest. The code must stay the author's: `signals.py`, `concepts.py`, `dq.py`, the pipeline definitions, `databricks.yml`, `dbt/models/` and the Airflow DAG are written by hand.
- Scope: stay inside the current sprint in `docs/PLAN.md`. Push back on anything out of scope.
- Write an ADR (`docs/adr/NNN-title.md`: Context, Decision, Alternatives considered, Consequences) the day a decision is made.

## Commands

- Tests: `uv run pytest`
- Lint and format: `uv run pre-commit run --all-files`
- Update pre-commit `uv run pre-commit autoupdate`
- Bundle: `databricks bundle validate -t dev`
- Bundle deploy: `databricks bundle deploy -t dev`
- Get esef filings: `uv run python -m extract.esef_filings`
- Get gleif isins: `uv run python -m extract.gleif_isin`
- Get tickers (after gleif): `uv run python -m extract.openfigi` (optional `OPENFIGI_API_KEY` env var)
- Get prices (from v0.2 Day 1): `uv run python -m extract.prices`
- Get ECB FX (from v0.2 Day 4): `uv run python -m extract.ecb_fx`
- dbt (from v0.2 Day 4): `uv run dbt build --project-dir dbt`
- Databricks: create catalog (SQL, the REST API fails on Free Edition, ADR-003): `CREATE CATALOG IF NOT EXISTS piotroski_dev`
- Databricks: create schema: `databricks schemas create bronze piotroski_dev`
- Databricks: create volume: `databricks volumes create piotroski_dev bronze landing MANAGED`

## Status

- Read docs/PLAN.md first; checkboxes are the status;
