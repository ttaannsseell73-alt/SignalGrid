from __future__ import annotations

import argparse
import csv
import hashlib
import io
import os
import shutil
import tempfile
import urllib.error
import urllib.request
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


BASE_URL = "https://data.binance.vision/data/futures/um/daily"


def archive_url(dataset: str, symbol: str, day: date, interval: str = "1m") -> str:
    symbol = symbol.upper()
    stamp = day.isoformat()
    if dataset == "klines":
        return (
            f"{BASE_URL}/klines/{symbol}/{interval}/"
            f"{symbol}-{interval}-{stamp}.zip"
        )
    if dataset == "bookTicker":
        return (
            f"{BASE_URL}/bookTicker/{symbol}/"
            f"{symbol}-bookTicker-{stamp}.zip"
        )
    raise ValueError(f"unsupported dataset: {dataset}")


def _download(url: str, destination: Path, timeout: int = 60) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "SignalGrid/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as response, destination.open("wb") as out:
        shutil.copyfileobj(response, out, length=1024 * 1024)


def _verify_checksum(zip_path: Path, checksum_text: str) -> None:
    expected = checksum_text.strip().split()[0].lower()
    if len(expected) != 64:
        raise RuntimeError(f"invalid checksum for {zip_path.name}")
    digest = hashlib.sha256()
    with zip_path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    actual = digest.hexdigest().lower()
    if actual != expected:
        raise RuntimeError(
            f"checksum mismatch for {zip_path.name}: expected {expected}, got {actual}"
        )


def _download_verified(url: str, destination: Path) -> None:
    _download(url, destination)
    checksum_url = url + ".CHECKSUM"
    checksum_path = destination.with_suffix(destination.suffix + ".CHECKSUM")
    try:
        _download(checksum_url, checksum_path)
        _verify_checksum(destination, checksum_path.read_text(encoding="utf-8"))
    finally:
        checksum_path.unlink(missing_ok=True)


def _numeric_first(value: str) -> bool:
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


def _append_zip_csv(zip_path: Path, writer: csv.writer) -> int:
    written = 0
    with zipfile.ZipFile(zip_path) as archive:
        names = [n for n in archive.namelist() if n.lower().endswith(".csv")]
        if len(names) != 1:
            raise RuntimeError(
                f"{zip_path.name} must contain exactly one CSV, found {len(names)}"
            )
        with archive.open(names[0], "r") as raw:
            text = io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
            reader = csv.reader(text)
            for row in reader:
                if not row:
                    continue
                # Strip every per-archive header. Consolidated files intentionally
                # use the canonical headerless Binance layout expected by loaders.
                if not _numeric_first(row[0]):
                    continue
                writer.writerow(row)
                written += 1
    return written


def _days(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def download_dataset(
    *,
    dataset: str,
    symbol: str,
    start: date,
    end: date,
    output_csv: Path,
    interval: str = "1m",
) -> tuple[int, int]:
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=output_csv.name + ".",
        suffix=".tmp",
        dir=str(output_csv.parent),
    )
    os.close(fd)
    temp_output = Path(temp_name)

    archives = 0
    rows = 0
    try:
        with temp_output.open("w", newline="", encoding="utf-8") as out:
            writer = csv.writer(out)
            for day in _days(start, end):
                url = archive_url(dataset, symbol, day, interval)
                with tempfile.TemporaryDirectory(prefix="signalgrid-download-") as td:
                    zip_path = Path(td) / Path(url).name
                    try:
                        print(f"[download] {url}", flush=True)
                        _download_verified(url, zip_path)
                    except urllib.error.HTTPError as exc:
                        if exc.code == 404:
                            print(f"[skip] archive not published: {day}", flush=True)
                            continue
                        raise
                    rows += _append_zip_csv(zip_path, writer)
                    archives += 1
        if archives == 0 or rows == 0:
            raise RuntimeError(f"no {dataset} data downloaded for {symbol}")
        temp_output.replace(output_csv)
        return archives, rows
    except Exception:
        temp_output.unlink(missing_ok=True)
        raise


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Download checksum-verified Binance USD-M scalping datasets"
    )
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--end-date", help="UTC YYYY-MM-DD; default is yesterday")
    p.add_argument("--output-dir", default="data/scalping")
    args = p.parse_args(argv)

    if args.days < 1 or args.days > 90:
        p.error("--days must be between 1 and 90")

    end = (
        datetime.strptime(args.end_date, "%Y-%m-%d").date()
        if args.end_date
        else datetime.now(timezone.utc).date() - timedelta(days=1)
    )
    start = end - timedelta(days=args.days - 1)
    symbol = args.symbol.upper()
    output_dir = Path(args.output_dir)

    kline_path = output_dir / f"{symbol}-1m.csv"
    book_path = output_dir / f"{symbol}-bookTicker.csv"

    print(
        f"SignalGrid historical download: {symbol} {start.isoformat()} -> {end.isoformat()}",
        flush=True,
    )
    k_archives, k_rows = download_dataset(
        dataset="klines",
        symbol=symbol,
        start=start,
        end=end,
        output_csv=kline_path,
        interval="1m",
    )
    b_archives, b_rows = download_dataset(
        dataset="bookTicker",
        symbol=symbol,
        start=start,
        end=end,
        output_csv=book_path,
    )

    print(
        {
            "symbol": symbol,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "klines": {"archives": k_archives, "rows": k_rows, "path": str(kline_path)},
            "bookTicker": {"archives": b_archives, "rows": b_rows, "path": str(book_path)},
        },
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
