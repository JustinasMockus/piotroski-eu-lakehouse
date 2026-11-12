import csv
import hashlib
import json
import sys
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import requests

BASE_URL = "https://api.nasdaq.com/api/nordic"
USER_AGENT = "piotroski-eu-lakehouse/0.2 (personal research)"
BACKFILL_START = date(2019, 1, 1)


def load_watermarks(path: Path) -> dict[str, date]:
    """Last price date already landed, per ISIN."""
    if not path.exists():
        return {}
    watermarks: dict[str, date] = {}
    with path.open() as f:
        for line in f:
            if not line.strip():
                continue
            record = json.loads(line)
            isin = record["isin"]
            last = date.fromisoformat(record["last_date"])
            if isin not in watermarks or last > watermarks[isin]:
                watermarks[isin] = last
        return watermarks


def fetch_window(watermark: date | None, today: date) -> tuple[date, date] | None:
    """Date range still to download for one ISIN, or None when it is up to date."""
    start = BACKFILL_START if watermark is None else watermark + timedelta(days=1)
    end = today - timedelta(days=1)
    if start > end:
        return None
    return start, end


def latest_listings(landing_dir: Path) -> Path:
    files = sorted(landing_dir.glob("openfigi/ingest_date=*/isin_ticker.csv"))
    if not files:
        raise FileNotFoundError("no isin_ticker.csv in landing; run extract.openfigi first")
    return files[-1]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def append_manifest(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(record) + "\n")


def listed_isins(path: Path) -> list[str]:
    """Nasdaq Copenhagen common-stock ISINs, the same filter as silver.isin_listing (ADR-008)."""
    with path.open(newline="") as f:
        isins = {
            row["isin"]
            for row in csv.DictReader(f)
            if row["security_type"] == "Common Stock" and row["exch_code"] == "DC"
        }
    return sorted(isins)


def get_json(session, url: str, params: dict, retries: int = 4, sleep=time.sleep) -> dict:
    for attempt in range(retries):
        try:
            response = session.get(url, params=params, timeout=60)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as exc:
            status = getattr(exc.response, "status_code", None)
            permanent = status is not None and 400 <= status < 500 and status != 429
            if permanent or attempt == retries - 1:
                raise
            sleep(2**attempt)


def check_status(payload: dict) -> None:
    status = payload["status"]
    if status["rCode"] != 200:
        raise ValueError(f"Nasdaq API error {status['rCode']}: {status['bCodeMessage']}")


def price_rows(payload: dict) -> list[dict]:
    """Daily rows from a chart/download response; [] when the range had no trading."""
    check_status(payload)
    charts = payload["data"]["charts"]
    if charts is None:
        return []
    return charts["rows"]


def find_orderbook_id(payload: dict, isin: str) -> str | None:
    """Nasdaq orderbook id of the share with this ISIN, or None when Nasdaq doesn't list it."""
    check_status(payload)
    for group in payload["data"] or []:
        for instrument in group["instruments"]:
            if instrument["isin"] == isin and instrument["assetClass"] == "SHARES":
                return instrument["orderbookId"]
    return None


def write_json(payload: dict, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    tmp.write_text(json.dumps(payload))
    tmp.replace(dest)


def run(session, landing_dir: Path, today: date, delay: float = 1.0, sleep=time.sleep) -> dict:
    prices_dir = landing_dir / "prices"
    manifest_path = prices_dir / "manifest.jsonl"
    out_dir = prices_dir / f"ingest_date={today.isoformat()}"
    watermarks = load_watermarks(manifest_path)
    counts = {"new": 0, "up_to_date": 0, "no_data": 0, "not_found": 0, "failed": 0}

    for isin in listed_isins(latest_listings(landing_dir)):
        window = fetch_window(watermarks.get(isin), today)
        if window is None:
            counts["up_to_date"] += 1
            continue
        start, end = window

        try:
            search = get_json(session, f"{BASE_URL}/search", {"searchText": isin}, sleep=sleep)
            sleep(delay)
            orderbook_id = find_orderbook_id(search, isin)
            if orderbook_id is None:
                counts["not_found"] += 1
                continue
            params = {
                "assetClass": "SHARES",
                "fromDate": start.isoformat(),
                "toDate": end.isoformat(),
            }
            url = f"{BASE_URL}/instruments/{orderbook_id}/chart/download"
            payload = get_json(session, url, params, sleep=sleep)
            sleep(delay)
            rows = price_rows(payload)
        except (requests.RequestException, ValueError, KeyError) as exc:
            print(f"failed: {isin} {exc!r}", file=sys.stderr)
            counts["failed"] += 1
            continue

        if not rows:
            counts["no_data"] += 1
            continue

        dest = out_dir / f"{isin}_{start}_{end}.json"
        write_json(payload, dest)
        append_manifest(
            manifest_path,
            {
                "isin": isin,
                "orderbook_id": orderbook_id,
                "from": start.isoformat(),
                "to": end.isoformat(),
                "rows": len(rows),
                "last_date": max(row["dateTime"] for row in rows),
                "file": dest.name,
                "sha256": sha256_file(dest),
                "downloaded_at": datetime.now(UTC).isoformat(),
            },
        )
        counts["new"] += 1

    return counts


def main() -> None:
    with requests.Session() as session:
        session.headers["User-Agent"] = USER_AGENT
        counts = run(session, Path("data/landing"), date.today())
    print(" ".join(f"{key}={value}" for key, value in counts.items()))
    if counts["failed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
