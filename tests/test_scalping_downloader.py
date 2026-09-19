import csv
import zipfile
from datetime import date
from pathlib import Path

from signalgrid.ops.download_scalping_data import (
    _append_bookticker_sampled_zip,
    _append_zip_csv,
    _normalize_timestamp_ms,
    archive_url,
)


def test_archive_urls_are_binance_usdm_daily_paths():
    day = date(2026, 9, 18)
    assert archive_url("klines", "btcusdt", day) == (
        "https://data.binance.vision/data/futures/um/daily/klines/"
        "BTCUSDT/1m/BTCUSDT-1m-2026-09-18.zip"
    )
    assert archive_url("bookTicker", "btcusdt", day) == (
        "https://data.binance.vision/data/futures/um/daily/bookTicker/"
        "BTCUSDT/BTCUSDT-bookTicker-2026-09-18.zip"
    )


def test_zip_merge_strips_repeated_headers(tmp_path):
    source = tmp_path / "sample.csv"
    source.write_text(
        "open_time,open,high\n"
        "1000,1,2\n"
        "2000,2,3\n",
        encoding="utf-8",
    )
    archive = tmp_path / "sample.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.write(source, arcname="inside.csv")

    out = tmp_path / "out.csv"
    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        count = _append_zip_csv(archive, writer)

    assert count == 2
    assert out.read_text(encoding="utf-8").splitlines() == [
        "1000,1,2",
        "2000,2,3",
    ]


def test_bookticker_sampler_keeps_latest_row_per_minute_and_sorts(tmp_path):
    source = tmp_path / "book.csv"
    # Deliberately unsorted. Canonical columns:
    # update_id,bid,bid_qty,ask,ask_qty,transaction_time,event_time
    source.write_text(
        "update_id,best_bid_price,best_bid_qty,best_ask_price,best_ask_qty,transaction_time,event_time\n"
        "3,100.2,2,100.3,3,119000,119000\n"
        "1,100.0,1,100.1,1,1000,1000\n"
        "2,100.1,2,100.2,2,59000,59000\n"
        "4,100.3,4,100.4,4,61000,61000\n",
        encoding="utf-8",
    )
    archive = tmp_path / "book.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.write(source, arcname="inside.csv")

    out = tmp_path / "sampled.csv"
    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        count = _append_bookticker_sampled_zip(archive, writer)

    rows = list(csv.reader(out.open("r", newline="", encoding="utf-8")))
    assert count == 2
    assert [row[0] for row in rows] == ["2", "3"]
    assert [int(row[6]) for row in rows] == [59000, 119000]


def test_timestamp_normalizer_accepts_microseconds():
    assert _normalize_timestamp_ms("1750000000000") == 1750000000000
    assert _normalize_timestamp_ms("1750000000000000") == 1750000000000
