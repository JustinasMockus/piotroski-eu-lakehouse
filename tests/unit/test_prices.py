import json
from datetime import date

import pytest
import requests

from extract.prices import (
    fetch_window,
    find_orderbook_id,
    get_json,
    latest_listings,
    listed_isins,
    load_watermarks,
    price_rows,
    run,
    write_json,
)


def test_load_watermarks_missing_file_returns_empty(tmp_path):
    assert load_watermarks(tmp_path / "manifest.jsonl") == {}


def test_load_watermarks_keeps_latest_date_per_isin(tmp_path):
    path = tmp_path / "manifest.jsonl"
    records = [
        {"isin": "DK0000000001", "last_date": "2026-11-10"},
        {"isin": "DK0000000001", "last_date": "2026-11-04"},
        {"isin": "DK0000000002", "last_date": "2026-11-03"},
    ]
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n\n")

    assert load_watermarks(path) == {
        "DK0000000001": date(2026, 11, 10),
        "DK0000000002": date(2026, 11, 3),
    }


TODAY = date(2026, 11, 12)


@pytest.mark.parametrize(
    ("watermark", "expected"),
    [
        (None, (date(2019, 1, 1), date(2026, 11, 11))),  # first run: backfill
        (date(2026, 11, 6), (date(2026, 11, 7), date(2026, 11, 11))),  # incremental
        (date(2026, 11, 11), None),  # already has yesterday
        (date(2026, 11, 12), None),  # watermark ahead (clock skew)
    ],
)
def test_fetch_window(watermark, expected):
    assert fetch_window(watermark, TODAY) == expected


FIELDS = "lei,isin,figi,ticker,name,exch_code,security_type,market_sector\n"


def write_listings(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(FIELDS + "".join(row + "\n" for row in rows))


def test_listed_isins_keeps_copenhagen_common_stock_once(tmp_path):
    path = tmp_path / "isin_ticker.csv"
    write_listings(
        path,
        [
            "LEI1,DK0000000002,FIGI1,BBB,Bravo,DC,Common Stock,Equity",
            "LEI1,DK0000000002,FIGI2,BBB,Bravo,DC,Common Stock,Equity",  # same ISIN twice
            "LEI2,DK0000000001,FIGI3,AAA,Alpha,DC,Common Stock,Equity",
            "LEI3,DK0000000003,FIGI4,FND,Fund,DC,Open-End Fund,Equity",  # not common stock
            "LEI4,DK0000000004,FIGI5,OTC,Other,DD,Common Stock,Equity",  # not Copenhagen main
        ],
    )

    assert listed_isins(path) == ["DK0000000001", "DK0000000002"]


def test_latest_listings_picks_newest_snapshot(tmp_path):
    old = tmp_path / "openfigi" / "ingest_date=2026-10-16" / "isin_ticker.csv"
    new = tmp_path / "openfigi" / "ingest_date=2026-11-12" / "isin_ticker.csv"
    write_listings(old, [])
    write_listings(new, [])

    assert latest_listings(tmp_path) == new


def test_latest_listings_without_snapshot_fails_clearly(tmp_path):
    with pytest.raises(FileNotFoundError, match="extract.openfigi"):
        latest_listings(tmp_path)


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        return self.responses.pop(0)


def test_get_json_retries_server_error_then_returns_payload():
    session = FakeSession([FakeResponse({}, 503), FakeResponse({"ok": True})])
    waits = []

    assert get_json(session, "https://example.test", {}, sleep=waits.append) == {"ok": True}
    assert waits == [1]


def test_get_json_fails_fast_on_404():
    session = FakeSession([FakeResponse({}, 404), FakeResponse({"ok": True})])

    with pytest.raises(requests.HTTPError):
        get_json(session, "https://example.test", {}, sleep=lambda s: None)
    assert session.calls == 1


def payload(rcode=200, charts=None):
    return {"data": {"charts": charts}, "status": {"rCode": rcode, "bCodeMessage": None}}


def test_price_rows_returns_rows():
    rows = [{"dateTime": "2026-11-10", "close": "271.50"}]
    assert price_rows(payload(charts={"rows": rows})) == rows


def test_price_rows_no_trading_is_empty_not_error():
    assert price_rows(payload(charts=None)) == []


def test_price_rows_api_error_raises():
    with pytest.raises(ValueError, match="400"):
        price_rows(payload(rcode=400))


def search_payload(*instruments):
    groups = (
        [{"group": "Shares Main Market", "instruments": list(instruments)}] if instruments else None
    )
    return {"data": groups, "status": {"rCode": 200, "bCodeMessage": None}}


def instrument(isin, orderbook_id, asset_class="SHARES"):
    return {"isin": isin, "orderbookId": orderbook_id, "assetClass": asset_class}


def test_find_orderbook_id_picks_share_with_matching_isin():
    found = search_payload(
        instrument("XS0000000001", "TX1", "STRUCTURED_PRODUCTS"),
        instrument("DK0000000001", "TX2", "BONDS"),
        instrument("DK0000000001", "TX3"),
    )
    assert find_orderbook_id(found, "DK0000000001") == "TX3"


def test_find_orderbook_id_unknown_isin_is_none():
    assert find_orderbook_id(search_payload(), "DK0000000001") is None


def test_write_json_writes_payload_and_no_part_file(tmp_path):
    dest = tmp_path / "prices" / "a.json"

    write_json({"a": 1}, dest)

    assert json.loads(dest.read_text()) == {"a": 1}
    assert list(dest.parent.iterdir()) == [dest]


def test_run_lands_prices_then_rerun_makes_no_price_requests(tmp_path):
    write_listings(
        tmp_path / "openfigi" / "ingest_date=2026-10-16" / "isin_ticker.csv",
        [
            "LEI1,DK0000000001,F1,AAA,Alpha,DC,Common Stock,Equity",
            "LEI2,DK0000000002,F2,BBB,Bravo,DC,Common Stock,Equity",
        ],
    )
    rows = [{"dateTime": "2026-11-11", "close": "10.0"}, {"dateTime": "2026-11-10", "close": "9.0"}]
    first = FakeSession(
        [
            FakeResponse(search_payload(instrument("DK0000000001", "TX1"))),
            FakeResponse(payload(charts={"rows": rows})),
            FakeResponse(search_payload()),  # DK...02: Nasdaq doesn't know it
        ]
    )

    counts = run(first, tmp_path, TODAY, sleep=lambda s: None)

    assert counts == {"new": 1, "up_to_date": 0, "no_data": 0, "not_found": 1, "failed": 0}
    prices = tmp_path / "prices"
    assert (prices / "ingest_date=2026-11-12" / "DK0000000001_2019-01-01_2026-11-11.json").exists()
    manifest = [json.loads(line) for line in (prices / "manifest.jsonl").read_text().splitlines()]
    assert [(m["isin"], m["last_date"]) for m in manifest] == [("DK0000000001", "2026-11-11")]

    rerun = FakeSession([FakeResponse(search_payload())])  # only DK...02 is searched again
    counts = run(rerun, tmp_path, TODAY, sleep=lambda s: None)

    assert counts["up_to_date"] == 1 and counts["not_found"] == 1
    assert rerun.calls == 1
