# ADR-012: Price source (Nasdaq Nordic API), EPS-implied share count, incremental extraction

Status: accepted (2026-11-12)

## Context

Sprint v0.2 adds book-to-market (B/M) so the screen matches Piotroski's paper, which scores only the top B/M quintile. B/M needs two inputs the MVP doesn't have: daily prices for the 89 listed LEIs (ADR-008) and a share count per fiscal year.

The Day 1 spike on 2026-11-11 checked both:

- **Stooq** (the source PLAN.md assumed): the quote CSV endpoint (`/q/l/`) returns 404. The history CSV endpoint (`/q/d/l/`) answers with a JavaScript proof-of-work bot check. Getting past it would mean deliberately evading the site's bot protection, so Stooq is not an option.
- **Nasdaq Nordic** (`api.nasdaq.com/api/nordic/`, the JSON API behind the Nasdaq Nordic website) works without a key:
  - `search?searchText=<ISIN>` returns the instrument's `orderbookId` (e.g. `DK0010244508` → `TX2196`, Maersk B).
  - `instruments/<orderbookId>/chart/download?assetClass=SHARES&fromDate=…&toDate=…` returns daily bid, ask, open, high, low, close, average, volume, turnover and trades in DKK. One request returned Maersk B from 2019-01-02 to 2026-11-10 (1,966 rows).
  - A descriptive client name (`piotroski-eu-lakehouse/<version>`) works. A generic `python-requests` User-Agent has its connection reset.
- **Share counts tagged in the filings** are rare: `NumberOfSharesIssued` and `NumberOfSharesOutstanding` cover 4 of 302 LEIs in `silver.fact`. Most filers tag share counts per share class, and `parse_facts` drops dimensional facts (ADR-009).
- **EPS-implied shares** (profit attributable to owners / basic EPS, both required by IAS 33 for listed companies) cover 46 of 89 listed LEIs in at least one year. Of the remaining 43, 40 tag EPS and `ProfitLoss` but not `ProfitLossAttributableToOwnersOfParent`. They have no non-controlling interests to split out. 1 tags owners' profit without EPS, and 2 tag neither.

## Decision

- **Price source: the Nasdaq Nordic API.** `extract/prices.py` maps each listed ISIN to its `orderbookId` with `search`, then downloads daily prices with `chart/download`. ISINs come from the latest OpenFIGI snapshot (`silver.isin_listing` logic, ADR-008), so no ticker translation is needed.
- **Honest client:** a fixed User-Agent that names the project, no browser impersonation, no contact email. 1 request per second, retry with backoff (the ESEF extractor's pattern).
- **Incremental by watermark:** the manifest records the last price date per ISIN. A run requests `fromDate = watermark + 1 day` up to yesterday. A first run backfills from 2019-01-01. A run with nothing new requests nothing, so reruns are idempotent. Rows land as raw JSON responses, one file per ISIN per run, under `prices/ingest_date=YYYY-MM-DD/`.
- **Share count: EPS-implied.** `shares_implied = profit_owners / basic_eps`, where `profit_owners` is the first of `ProfitLossAttributableToOwnersOfParent` and `ProfitLoss` present (a fallback chain in `concepts.py`). This covers 86 of 89 listed LEIs. Where an input is missing, shares and market cap are null, never 0. A guard for EPS near zero is set on Day 2 with the real distribution.
- **Never commit price data.** Nasdaq's terms don't allow redistribution, so the yfinance rule from PLAN.md applies: prices live only in the volume and in tables.

## Alternatives considered

- **Stooq.** Behind a bot check (see Context).
- **A manual Nasdaq Nordic CSV export.** It works, but it's manual: no schedule, no incremental load, nothing for `extract.yml` to run. It stays the fallback if the API changes or blocks the project.
- **yfinance.** It needs ticker translation (`NOVO-B.CO`), and Yahoo's terms forbid redistribution. The Nasdaq API has the same redistribution limit, without the translation.
- **Rate-limited price APIs (EODHD, Alpha Vantage free tier).** They need a key and have small daily quotas. That's planned as v0.3, a cross-check source, not a dependency for this sprint.
- **Share counts per share class from bronze.** Sum the class members of `NumberOfSharesIssued`, subtract treasury shares. Axes and members vary by filer (often company extensions), and coverage is unknown. About a day of work. Kept as a fallback if EPS-implied counts fail a cross-check.
- **Nasdaq `instruments/<id>/summary` share count.** It gives only today's count for one share series, so applied to past years it would be wrong, and dual-class companies would need summing. Useful as a cross-check for the latest year only.

## Consequences

- **It's an undocumented API.** It can change or block automated clients without notice. The extractor fails loudly on unexpected responses rather than landing empty files. The fallback is the manual CSV export, which goes into the same landing layout.
- **Untested from GitHub runners.** Datacenter IPs are sometimes blocked. If `extract.yml` can't reach the API, extraction runs on the PC (ADR-001's fallback).
- **The EPS-implied count is a weighted average for the year**, not the count at year end. Buybacks and share issues during the year shift it a little.
- **The `ProfitLoss` fallback assumes no non-controlling interests.** A filer with NCI that doesn't tag the split gets a slightly overstated share count.
- **Dual-class companies:** EPS covers all share classes, so the implied count is company-wide. Market cap uses one listed series' price for the whole count. When the classes trade at different prices (Maersk A vs B), market cap is approximate.
- **Survivorship:** prices exist only for ISINs listed today (ADR-008). That's fine for a current screen, but it biases any backtest.
