import csv
import zipfile
from datetime import date
from pathlib import Path

from signalgrid.ops.download_scalping_data import (
    _append_zip_csv,
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
