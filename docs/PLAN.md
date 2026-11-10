# Piotroski EU Lakehouse — Project Plan

Oct 13, 2026 · @Justinas

## Scope and MVP definition of done

The MVP is due by 2026-10-19. It produces a gold table with a Piotroski F-score (0–9) for every company in one EU country, built from free ESEF filings. It is deployed to Databricks by GitHub Actions and documented well enough to walk through in a 20-minute interview.

**Definition of done (all must be true by day 7):**

- [x] One command (`databricks bundle deploy -t prod` run by CI on merge to `main`) deploys everything. Nothing is created by hand in the UI except the workspace itself.
- [x] Extraction lands raw xBRL-JSON files plus a manifest in a Unity Catalog volume. It is idempotent: re-running it downloads nothing new.
- [x] Bronze, silver and gold tables exist, using Auto Loader and a declarative pipeline, with data quality expectations on silver.
- [x] `gold.piotroski_score` has all 9 signals as separate columns, plus `data_completeness`, so the reader can see which signals were missing rather than silently scored 0.
- [x] Accounting identity reconciliation (Assets = Equity + Liabilities, within tolerance) runs on silver. Failures are counted in a DQ table, not hidden.
- [x] Unit tests for the pure-Python logic (concept mapping, signal maths) run in CI on every PR.
- [x] README with an architecture diagram, a "how to run" section, a top-10 output sample, known limitations, and links to at least 5 ADRs.
- [x] A dashboard (Databricks AI/BI) showing the score distribution and DQ failure counts.

**Out of scope for the MVP:** Magic Formula, Altman Z, Beneish M, prices, multi-country, dbt, Terraform, AWS, the rate-limited ticker API. Each one is a post-MVP milestone (last section). Bring anything new in only when the definition of done above is green. The MVP is done (v0.1.1); [Sprint v0.2](#sprint-v02-nov-1117-2026) supersedes this list for prices and dbt.

**Realism check:** at 3–5 h/day, a week is about 25–30 hours. That's enough only if silver mapping is time-boxed. Mapping IFRS tags to Piotroski inputs is where the week will slip (day 4). The rule: map the top concepts, mark the rest as missing, and move on.

## Constraints and data sources

Free Edition gives serverless compute only, restricted outbound internet (loosened, not removed, by LinkedIn verification), a cap of 5 concurrent job tasks, and non-commercial use ([Databricks docs](https://docs.databricks.com/aws/en/getting-started/free-edition-limitations)). Data volume is not a constraint: one country's ESEF filings is a few hundred MB of JSON.

### Day-1 spike: does your verified workspace reach the sources?

Run this in a serverless notebook. Each URL either returns an HTTP status or fails with a connection or DNS error. A failure means blocked, and that source's extraction moves to GitHub Actions.

```python
import requests

for url in [
    "https://filings.xbrl.org/api/filings?page[size]=1",
    "https://leidata-preview.gleif.org/api/v1/lei-records?page[size]=1",
    "https://data-api.ecb.europa.eu/service/data/EXR/D.USD.EUR.SP00.A?lastNObservations=1",
    "https://stooq.com/q/l/?s=aapl.us&f=sd2t2ohlcv&h&e=csv",
]:
    try:
        r = requests.get(url, timeout=15)
        print("OK  ", r.status_code, url)
    except Exception as e:
        print("FAIL", type(e).__name__, url)
```

Record the output verbatim in ADR-001. The design works either way. Default: **extract outside Databricks** (a GitHub Actions job, or your PC as a fallback) and upload to a UC volume with `databricks fs cp`. That's the pattern a real firewalled bank lakehouse uses, so it's a better interview story than "the notebook called requests".

### Sources

| Source | What it gives | Role | Gotchas |
| --- | --- | --- | --- |
| [filings.xbrl.org API](https://filings.xbrl.org/docs/about) | ESEF annual reports as xBRL-JSON, indexed by LEI, period and country | Primary fundamentals (MVP) | Not complete coverage. IFRS tags plus company extensions. Each filing has current and prior year, so one filing yields both Piotroski periods. |
| GLEIF LEI–ISIN mapping file | LEI → ISIN | Join key to tickers (MVP) | Many-to-many (one LEI, many ISINs). Needs a dedup rule. |
| ~~Exchange equity list for the chosen country~~ (replaced by OpenFIGI, ADR-008) | ISIN, ticker, market, sector | Universe and ticker (MVP) | The Nasdaq Copenhagen screener export has no ISIN or ticker. |
| [OpenFIGI mapping API](https://www.openfigi.com/api/documentation) | ISIN → FIGI, ticker, exchange code, security type | Ticker per listing (MVP, best-effort, ADR-008) | Free; 25 req/min without a key. Bloomberg-style tickers. Listed equities only by default. |
| ECB FX reference rates | Daily EUR crosses | Currency normalisation (MVP if the country isn't euro) | Only matters for non-EUR filers. |
| Stooq | Daily prices | Post-MVP (market cap, Magic Formula) | Unofficial, can change without notice. |
| Rate-limited fundamentals API (FMP / EODHD / Alpha Vantage free tier) | Per-ticker statements | Post-MVP source #3, a cross-check | Free tiers have small daily quotas. Check current terms and don't publish their data in the repo. |
| yfinance | Per-ticker statements | Local cross-check only | Yahoo terms forbid redistribution. Never commit its output. |

### Choosing the country (day 1, by data, not guesswork)

Query the filings.xbrl.org API, count filings per country for the last 2 fiscal years, and pick the country with the most filings that have 0 validation errors. Your preference for the biggest market is right, but coverage on this site varies by country: some national filing registries are hard to scrape, so a big economy can be thin. The count query is itself a commit (`notebooks/00_country_profiling`), and the result goes into ADR-002.

## Architecture

Extraction runs outside Databricks and only lands files. That keeps it working even if the day-1 egress test shows the sources are blocked from the workspace. Everything from bronze onwards is declarative and deployed by CI.

&#91;embedded content: target architecture · extract outside, medallion inside, CI deploys\]

The arrow from Deploy into the workspace is the only way code reaches prod; nothing is clicked together in the UI.

## Local setup, repo layout and AI agents

### Tools (Linux or Windows, \~1.5 h)

| Tool | Why | Check |
| --- | --- | --- |
| Python matching your serverless environment | Local tests behave like the cluster | `import sys; print(sys.version)` in a workspace notebook, then pin it with uv |
| [uv](https://docs.astral.sh/uv/) | Env, lockfile, fast CI installs | `uv --version` |
| Databricks CLI (recent; bundles are now called Declarative Automation Bundles) | Bundle validate, deploy, run; `fs cp` to volumes | `databricks -v`, then `databricks auth login --host <workspace-url>` |
| Java 17 | Local PySpark for unit tests | `java -version` |
| pre-commit + ruff + gitleaks | Lint, format, block secrets before they reach Git | `pre-commit run --all-files` |
| GitHub CLI | PRs from the terminal | `gh auth status` |

**VS Code extensions:** Databricks (official: bundle explorer, Databricks Connect run/debug), Python + Pylance, Ruff, YAML (Red Hat; validates `databricks.yml` against the bundle JSON schema), GitLens (optional), Claude Code. Nothing else.

### Repo layout

```text
piotroski-eu-lakehouse/
├── databricks.yml              # bundle root: targets dev / prod, variables
├── resources/                  # jobs, pipelines, volumes as YAML
│   ├── pipeline_fundamentals.yml
│   └── job_daily.yml
├── extract/                    # runs OUTSIDE Databricks (GH Actions / PC)
│   ├── esef_filings.py         # list + download xBRL-JSON, write manifest
│   ├── prices.py               # Sprint v0.2: daily prices, watermark per ticker
│   ├── ecb_fx.py               # Sprint v0.2: ECB reference rates
│   ├── gleif_isin.py
│   └── upload_to_volume.sh
├── src/piotroski/              # pure Python, imported by pipeline + tests
│   ├── concepts.py             # IFRS tag -> canonical field mapping
│   ├── signals.py              # the 9 signals as functions on DataFrames
│   └── dq.py                   # reconciliation rules
├── pipelines/                  # declarative pipeline definitions (bronze/silver/gold)
├── dbt/                        # Sprint v0.2: dbt-databricks marts on gold
├── airflow/                    # Sprint v0.2 (optional): local Docker Compose DAG
├── notebooks/                  # exploration only; never imported
├── tests/unit/  tests/fixtures/  # small hand-made JSON, NOT real downloads
├── docs/adr/  docs/architecture.md  docs/PLAN.md
├── .github/workflows/  ci.yml  deploy.yml  extract.yml
├── .claude/agents/  planner.md  reviewer.md
├── CLAUDE.md  README.md  pyproject.toml  uv.lock  .pre-commit-config.yaml  .gitignore
```

The rule that makes this testable: business logic lives in `src/piotroski/` as functions of `DataFrame -> DataFrame`. Pipeline files only wire them together. Notebooks are scratch space.

### The two agents

- **`planner.md`** reads `docs/PLAN.md` and the ADRs. It breaks the next task into steps and acceptance criteria, and it does **not** write code. It pushes back on scope creep against the definition of done.
- **`reviewer.md`** reviews a diff (`git diff main...HEAD`) against a fixed checklist: idempotency, secrets, tests present, naming, ADR needed?, can-you-explain-it. It answers with blocking and non-blocking comments.
- **`CLAUDE.md`** holds house rules: never commit data or secrets, conventional commits, Python version, "ask before writing more than \~30 lines", explain any API it suggests. That last rule keeps the code yours.

**Write by hand:** `signals.py`, `concepts.py`, `dq.py`, the pipeline definitions, `databricks.yml`. **Fine to pair on with AI:** GitHub Actions YAML, README prose, diagram code, the extractor's retry and pagination boilerplate.

## Week 1: day-by-day MVP plan (Oct 14–20)

Each day ends with a merged PR to `main`. If a day overruns by more than 2 hours, cut scope from that day. Don't push the remaining days back.

### Day 1 (Wed Oct 14): foundations and facts

- [x] Create the GitHub repo (public, MIT licence, `.gitignore` for Python plus `data/`, `*.json` outside `tests/fixtures`). Protect `main`: PR required, CI must pass.
- [x] Install the local tools; run `databricks auth login`; run the egress spike notebook → **ADR-001** (where extraction runs).
- [x] Run country profiling against the filings API → **ADR-002** (which country and why, with the counts).
- [x] `uv init`, pre-commit with ruff + gitleaks, an empty `src/piotroski/`, one passing dummy test.
- [x] Write `CLAUDE.md` and the two agent files; copy this plan to `docs/PLAN.md`.

### Day 2 (Thu Oct 15): extraction to landing

- [x] `extract/esef_filings.py`: page through the API filtered by country, download each filing's xBRL-JSON, write `manifest.jsonl` (LEI, period end, filing id, URL, sha256, downloaded\_at).
- [x] Idempotency: skip filings already in the manifest. Retry with backoff (file downloads; a failed listing page stops the run, and a rerun resumes from the manifest). Polite rate limit (1 request/s).
- [x] `extract/gleif_isin.py` and the exchange list CSV (a manual download is fine; record the date). The exchange list has no ISIN or ticker, so choosing that source moves to Day 3.
- [x] Create a UC volume `landing` per target; upload with `databricks fs cp -r`. Layout: `/Volumes/<catalog>/<schema>/landing/esef/ingest_date=YYYY-MM-DD/`. Done for `dev`; `prod` is created before the first prod deploy (ADR-003).

### Day 3 (Fri Oct 16): bronze with Auto Loader

- [x] Bronze table `bronze.esef_filing_raw`: Auto Loader, each file read **whole as text** plus `_metadata.file_path` and ingest timestamp. Don't infer a schema from xBRL-JSON: facts are a map keyed by fact id, and inference explodes into thousands of columns → **ADR-004**.
- [x] Pick an ISIN/ticker source for the exchange list and record it in a new ADR. → **ADR-008**: OpenFIGI mapping of GLEIF `DK` ISINs, `extract/openfigi.py`. The Day 2 Nasdaq Copenhagen screener export (117 rows) has no ISIN or ticker, so it cannot join to GLEIF. Avoid joining on company name. If no source turns up within the time-box, make the ticker best-effort and say so in the ADR.
- [x] `bronze.lei_isin` and `bronze.isin_listing` (OpenFIGI, `openfigi/`) as CSV loads. The Nasdaq screener export is dropped (ADR-008). `lei_isin/` also holds `source.json`, so filter on `*.csv`.
- [x] First `databricks.yml` with a `dev` target; deploy from VS Code; pipeline runs green. The catalog comes from SQL `CREATE CATALOG`; check whether the bundle `catalogs` resource works on Free Edition (ADR-003). Auto Loader on `esef/` needs `pathGlobFilter="*.json"` to skip `manifest.jsonl`. Result: the bundle `catalogs` resource fails like the REST API, so the SQL bootstrap stays (ADR-003). Schemas and the volume move into the bundle on Day 6.

### Day 4 (Sat Oct 17): silver, the hard day (time-box: 6 h)

- [x] Make `src/piotroski/` importable from the pipeline source files. Bundle-deployed pipeline code does not see `src/` on its path by default (flagged on Day 3). Do this first: `concepts.py` is the first module the pipeline imports. Then move `with_lineage` from `pipelines/bronze.py` into `src/piotroski/` with a fixture test for the `ingest_date` regex.
- [x] Decide how silver and gold publish next to bronze: one pipeline with fully qualified table names (`silver.fact`) or one pipeline per layer. The pipeline's default schema is `bronze`. Record the decision in ADR-003 Consequences. Result: `src/` ships as a versioned wheel; dev-mode pipelines reused a stale environment, so the dev target sets `presets.pipelines_development: false` (ADR-010). One pipeline with qualified names (ADR-003).

- [x] `silver.fact`: parse with `from_json` to `MAP<STRING, STRUCT<value, decimals, dimensions MAP>>`, explode, and keep **non-dimensional** facts only (no segment or axis members). That gives consolidated totals.
- [x] Parse periods: instant vs. duration. Map each to fiscal year end.
- [x] `concepts.py`: a mapping table from canonical field to an ordered list of `ifrs-full:` concepts (first hit wins). Start with: `ProfitLoss`, `Assets`, `CashFlowsFromUsedInOperatingActivities`, `CurrentAssets`, `CurrentLiabilities`, `NoncurrentLiabilities` (proxy for long-term debt; record it in ADR), `Revenue`, `GrossProfit` / `CostOfSales`, `ProceedsFromIssuingShares`.
- [x] `silver.fundamentals`: one row per LEI × fiscal year, a column per canonical field, currency column, source filing id. Dedupe restatements: the newest filing wins for a given year. Result: 525 annual filings (7 trailing-twelve-month interims excluded), 815 rows for 302 LEIs (ADR-009).
- [x] Expectations: `Assets > 0`, the currency is ISO, fiscal year within range → drop or quarantine (decide and write it down). Result: `expect_all_or_drop`; counts in the event log for Day 5 DQ.

### Day 5 (Sat Oct 17, a day early; the MVP date moved to Oct 19): gold and tests

- [x] `signals.py`: the 9 signals, each returning 1 / 0 / null. Null means missing input, never 0 → **ADR-005**. Result: ratios use end-of-year assets, so change signals need only t and t−1; gross profit falls back to `revenue − abs(cost_of_sales)` (33 LEI-years).
- [x] `gold.piotroski_score`: signals, `f_score = sum of non-null`, `signals_available`, one row per LEI × fiscal year. Listings (ISIN, ticker) join one-to-many from `silver.isin_listing` filtered to `Common Stock`; never force 1:1 (dual-class shares, ADR-008). Result: 815 rows (same as silver); 245 fully scored, 297 first years at 4 signals. Listings are an array per LEI, from the latest OpenFIGI snapshot.
- [x] Unit tests with hand-made fixtures: one company per edge case (all 9 met, missing gross profit, negative equity, restated year). Result: `test_signals.py`, `test_listings.py`, `test_dq.py`. Negative equity is tested in the identity check, because no signal uses equity. The restated year is covered by `latest_filing`, plus a gap-year test.
- [x] `dq.py` + `gold.dq_results`: balance-sheet identity check, % of companies with all 9 signals, unmapped LEIs, % of ESEF LEIs with at least one `DC` common-stock listing (with the misses listed). Result: identity 9 of 731 checked fail (1.2%), duplicate conflicts in 7 rows, 13 of 315 LEIs unmapped, 89 of 315 LEIs with a listing (ADR-008). A materialized view of the latest run; history and event-log drop counts move to Day 6.

### Day 6 (Sun Oct 18): CI/CD and orchestration

- [x] `ci.yml` on PR: ruff, pytest, `databricks bundle validate -t dev`. Result: pre-commit (ruff), gitleaks-action, pytest, then `bundle validate` for dev and prod as the CI service principal; `test` is a required check on `main`.
- [x] Move the `bronze`/`silver`/`gold` schemas and the landing volume into the bundle: `databricks bundle deployment bind` the existing objects, and check dev-mode naming first (ADR-003, moved from Day 3). Result: dev mode prefixes schema names (`dev_<user>_bronze`), so they are bundle resources in prod only, with `prevent_destroy`; dev keeps the CLI-created ones, nothing bound.
- [x] `deploy.yml` on merge to `main`: `bundle deploy -t prod`, then `bundle run` of the job. Auth via a GitHub environment secret → **ADR-006** (service principal OAuth if Free Edition allows it, otherwise a short-lived PAT; write down which and why). Result: service principal OAuth M2M works on Free Edition (ADR-006); prod catalog bootstrapped with SQL. First prod deploy and job run green on 2026-10-18, after a bootstrap: deploy only, grants, upload, then `RUN_AFTER_DEPLOY` (ADR-003). The SDK auth error prints workspace and account IDs, so both workflows mask them (ADR-006).
- [ ] ~~`extract.yml`~~ (cut): manual `workflow_dispatch` plus a monthly cron that runs the extractor and uploads to the prod volume. Not in the DoD: cut first if the day runs over. Cut on 2026-10-18. When it returns, it must fetch `manifest.jsonl` from the volume first, or a fresh runner re-downloads everything. → Returns in Sprint v0.2 Day 1, on a weekly cron.
- [x] A job in `resources/job_daily.yml`: pipeline refresh → DQ task → failure email notification. The DQ task appends `gold.dq_results` to a history table (one row per run per rule) and adds the expectation drop counts from the event log (moved from Day 5, ADR-009). Result: `piotroski_daily` (refresh → `dq_history`), event log published as `bronze.pipeline_event_log`, `gold.dq_results_history` tagged with `run_id`; first dev run green, 8 rules (3 expectations, 0 drops).

### Day 7 (Mon Oct 19): make it presentable

- [x] README: problem, the diagram, a results sample, how to run, limitations, an ADR index.
- [x] AI/BI dashboard: score distribution, top 20, DQ failures over runs.
- [x] Ask the reviewer agent for a full-repo review; fix the blocking comments.

## Sprint v0.2 (Nov 11–17, 2026)

**Goal:** close the dbt, incremental/CDC/SCD2 and orchestration gaps on the existing repo. These are the skills most often asked for in data-engineering job ads, and the CDC/SCD2 work maps directly onto SQL Server/SSIS → Databricks migrations. 7 days × 2–4 h, about 22 h.

**Rules:** each day ends with a merged PR. If a day overruns by more than 1 h, cut scope from that day; don't push the remaining days back. **Days 2–3 are never cut.** Cut order if behind: Day 6 (Airflow) first, then `dbt build` in CI (Day 5).

### Day 1 (3 h): prices extraction and a scheduled extract

- [ ] 30-min spike: confirm a free daily price source still works for the ~89 listed DK tickers (Stooq first; it may now block scripted CSV downloads). Fallback: a manual Nasdaq Nordic CSV, recorded in the ADR. Never commit yfinance output.
- [ ] Same spike: count coverage of `ifrs-full:NumberOfSharesIssued` and `ifrs-full:NumberOfSharesOutstanding` in `silver.fact`. Market cap needs a share count, and silver maps none yet.
- [ ] `extract/prices.py`: incremental by watermark (last date per ticker from the manifest), idempotent, 1 req/s.
- [ ] Bring back `extract.yml`: `workflow_dispatch` plus a weekly cron. Pull `manifest.jsonl` from the volume first, then extract, then upload. Drop the "Extraction is manual" limitation from the README.
- [ ] **ADR-012**: price source and incremental strategy.

### Day 2 (3–4 h): incremental silver with AUTO CDC

- [ ] `bronze.price_raw` via Auto Loader.
- [ ] `silver.price_daily`: AUTO CDC (`dp.create_auto_cdc_flow`), keys `(ticker, date)`, sequence by ingest timestamp, SCD type 1. Late corrections overwrite.
- [ ] `shares_outstanding` canonical field in `concepts.py` and `silver.fundamentals` (concepts from the Day 1 spike). Missing means null market cap, never 0.
- [ ] Unit test for the price-parsing function. Rerun the extract and prove there are no duplicates (DQ rule: unique ticker + date).

### Day 3 (3 h): SCD2 listing dimension and a quarantine table

- [ ] `silver.dim_listing`: AUTO CDC stored as SCD type 2 over OpenFIGI snapshots (ticker or exchange changes become history rows).
- [ ] Replace the silent `expect_all_or_drop` on `silver.fundamentals` with a quarantine table (failed rows plus the reason) that feeds `gold.dq_results`.
- [ ] **ADR-013**: drop vs quarantine.

### Day 4 (3–4 h): FX and dbt on gold

- [ ] `extract/ecb_fx.py` (DKK crosses, idempotent) plus a silver FX table. Convert equity to DKK at the fiscal-year-end rate, because some DK filers report in EUR or USD.
- [ ] `dbt/` project: dbt-databricks against the Free Edition serverless SQL warehouse, profile from env vars (no secrets in the repo).
- [ ] Sources: `silver.fundamentals`, `silver.price_daily`, silver FX, `gold.piotroski_score`.
- [ ] Models: `market_cap`, `book_to_market`, `piotroski_screen` (top B/M quintile, F-score ≥ 8). This makes the screen match the original paper.
- [ ] Tests: unique/not_null on keys, accepted_values on `f_score` 0–9, one singular test (market cap > 0).
- [ ] **ADR-014**: FX conversion and the book-to-market definition.

### Day 5 (3 h): dbt in CI and in the job

- [ ] CI: `dbt deps && dbt parse`. Optional (cut first): `dbt build` against dev if the warehouse cold start is acceptable.
- [ ] Bundle job: pipeline refresh → `dbt_task` (dbt build) → `dq_history`.
- [ ] **ADR-015**: what goes in declarative pipelines vs dbt, and why (ingestion and CDC in the pipeline, business marts in dbt).

### Day 6 (2–3 h, cut first): Airflow locally

- [ ] Docker Compose Airflow on the home PC. One DAG: extract → upload → `DatabricksRunNowOperator` on `piotroski_daily`.
- [ ] Token from an Airflow connection only, never in the repo. A short README section with a scrubbed screenshot.
- [ ] **ADR-016**: Airflow vs GitHub Actions cron vs Databricks Jobs (GitHub Actions stays the default; say why).

### Day 7 (2–3 h): make it presentable

- [ ] README: updated diagram, screen results, ADR index, "what broke this sprint".
- [ ] README: a feature → migration pattern table (AUTO CDC/MERGE ↔ SSIS incremental loads, SCD2 ↔ slowly changing dimension, quarantine ↔ error-output rows).
- [ ] Dashboard: a screen tile.
- [ ] Full-repo review by the reviewer agent; fix the blocking items.
- [ ] Tag `v0.2.0`.

## Testing, data quality, CI/CD and monitoring

The testing strategy has three layers, and each catches a different class of bug. Lean on the reconciliation layer hardest: it's your DataCompy experience applied to a new domain.

| Layer | What | Where it runs | Catches |
| --- | --- | --- | --- |
| Unit | pytest on `src/piotroski/` with local PySpark and hand-made fixtures | GitHub Actions, every PR | Wrong signal maths, broken concept mapping |
| Pipeline expectations | Declarative pipeline expectations on silver (warn / drop / fail per rule) | Databricks, every run | Bad rows from messy filings |
| Reconciliation | Assets = Equity + Liabilities (±1%); current assets ≤ total assets; spot check of 3 companies against their PDF reports | Databricks DQ task + a manual notebook | Wrong concept picked, unit or scale errors, dimensional facts leaking in |

### Environments on one Free Edition workspace

There are two bundle targets. `dev` deploys in development mode, so resource names get your username as a prefix and schedules are paused. `prod` is deployed only by CI. Data is separated by catalog per target if Free Edition lets you create catalogs, otherwise by schema prefix (`dev_bronze`, `prod_bronze`). That decision goes in **ADR-003**.

### CI/CD flow

1. Feature branch → PR → `ci.yml`: ruff, pytest, `bundle validate`.
2. Optional: comment `/deploy-dev` or run a manual workflow to deploy the PR to `dev`.
3. Merge → `deploy.yml`: `bundle deploy -t prod` → `bundle run` of the job → workflow fails if the job fails.
4. `extract.yml` runs separately on a weekly cron (Sprint v0.2). Fundamentals change once or twice a year, but prices change daily; weekly keeps prices fresh enough for a yearly screen and picks up late filers.

### Monitoring (scaled to a portfolio project)

- Job failure → email notification (in the job YAML).
- `gold.dq_results`: one row per run per rule, with counts. The dashboard shows the trend.
- The pipeline event log for expectation pass/fail rates.
- `extract/` writes a run summary (new / skipped / failed filings) into the manifest and the GitHub Actions job summary.
- Use system tables only if Free Edition exposes them; check before planning around them.

## ADRs, commit hygiene and security

Write ADRs as short Markdown files in `docs/adr/NNN-title.md` with four headings: Context, Decision, Alternatives considered, Consequences. Keep each one to a page or less. Write it the day the decision is made, while the reason is still fresh.

| ADR | Decision | Day |
| --- | --- | --- |
| 001 | Extraction runs outside Databricks (egress test result) | 1 |
| 002 | Country choice, with filing counts | 1 |
| 003 | Environment isolation on one workspace: catalogs or schema prefixes | 3 |
| 004 | Bronze stores raw xBRL-JSON as text; parsing happens in silver | 3 |
| 005 | Missing input = null signal, never 0; `signals_available` exposed | 5 |
| 006 | CI auth method for Free Edition | 6 |
| 007 | Long-term debt proxy and equity-issuance definition | 4 |
| 008 | Ticker source: OpenFIGI mapping, listing grain per ISIN | 3 |
| 009 | Silver fact selection rules (prefixes, scope, annual filings, duplicates, restatements) | 4 |
| 010 | `src/` shipped as a versioned wheel; dev-mode pipelines off | 4 |
| 011 | Dashboard as code in the bundle | 7 |
| 012 | Price source and incremental extraction strategy (planned) | v0.2 Day 1 |
| 013 | Drop vs quarantine for failed silver rows (planned) | v0.2 Day 3 |
| 014 | FX conversion and book-to-market definition (planned) | v0.2 Day 4 |
| 015 | Declarative pipelines vs dbt: what goes where (planned) | v0.2 Day 5 |
| 016 | Airflow vs GitHub Actions cron vs Databricks Jobs (planned) | v0.2 Day 6 |
| 017+ | One per Free Edition limit you actually hit, with the error message | as hit |

### Commit and PR hygiene

- Conventional commits (`feat(silver): map current assets concepts`). One logical change per commit; one day's theme per PR.
- The PR template has three questions: what changed, how you tested it, and whether an ADR is needed.
- Squash-merge to `main` so history reads as one line per feature. Tag `v0.1.0` at the MVP.

### Security and data rules (non-negotiable)

- No tokens anywhere in the repo. Locally use `~/.databrickscfg` from `auth login` (outside the repo). In CI use a GitHub environment secret. gitleaks runs in pre-commit **and** in CI.
- No downloaded data in Git: real filings, yfinance output and API responses stay in the volume or in a gitignored `data/`. Test fixtures are hand-written and tiny.
- The workspace URL is fine to publish. Workspace IDs, user emails and tokens are not; scrub screenshots before they go in the README.

## After the MVP

Each milestone is about 2–4 days and starts only after the previous one is merged and tagged.

0. **v0.1.1: MVP follow-ups** (from the Day 7 review; about half a day, do first).
   - [x] `jobs/dq_history.py`: a repair run reuses `run_id` and appends the rows again. Result: `overwrite` with `replaceWhere run_id = …` swaps the run's rows atomically, which is simpler than a MERGE. A dev repair run left 8 rows for 8 rules.
   - [x] `resources/dashboard_piotroski.yml`: add a `permissions` block. Right now only workspace admins can open the prod dashboard. Result: `CAN_READ` for the built-in `users` group, so there is no email and no variable.
   - [x] `tests/unit/test_signals.py`: add a zero-denominator case. Result: `current_liabilities = 0` was already covered (`NOGP`); `ZERO` adds `assets = 0`, which nulls the four asset-based signals.
   - [x] README: a dashboard screenshot in `docs/img/`, with the address bar cropped (the URL carries the workspace ID).
   - [x] Dashboard: a "Not financial advice, see the README" text widget. Result: added straight to the JSON as a full-width text widget at the top, as a `####` heading so it reads one size above body text.
   - [x] `deploy.yml`: `paths-ignore` for docs-only changes (`docs/**`, `**.md`), so a README edit doesn't redeploy prod and re-run the job. A push that changes code as well still deploys.

1. **v0.2: prices, CDC/SCD2, dbt and orchestration** → [Sprint v0.2](#sprint-v02-nov-1117-2026) above.
2. **v0.3: rate-limited ticker API (your idea).** A queue table of tickers, a daily job pulling N tickers within the free quota, watermark and retry state stored in Delta. It shows backfill under quotas, a very common real-world pattern. Use it as a cross-check source against ESEF (source-vs-source reconciliation report).
3. **v0.4: more scores.** Altman Z, Magic Formula, Beneish M on the same silver. If the silver model was right, each one is about a day. If it's painful, that's a talking point about the model too.
4. **v0.5: second country.** Tests whether `concepts.py` generalises. Expect new extension tags.
5. **Backlog: Terraform** for workspace objects, catalogs and grants. Low value here: Free Edition blocks catalog creation over the API (ADR-003), and bundles already deploy everything else. dbt moved into Sprint v0.2.
6. **Optional: AWS.** S3 landing + Lambda extractor via Terraform, only if a Free Edition limit forces it.
