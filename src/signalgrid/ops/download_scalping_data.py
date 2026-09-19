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
from itertools import chain


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
    if dataset == "aggTrades":
        return (
            f"{BASE_URL}/aggTrades/{symbol}/"
            f"{symbol}-aggTrades-{stamp}.zip"
        )
    if dataset == "bookDepth":
        return (
            f"{BASE_URL}/bookDepth/{symbol}/"
            f"{symbol}-bookDepth-{stamp}.zip"
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


def _single_csv_name(archive: zipfile.ZipFile, zip_name: str) -> str:
    names = [n for n in archive.namelist() if n.lower().endswith(".csv")]
    if len(names) != 1:
        raise RuntimeError(
            f"{zip_name} must contain exactly one CSV, found {len(names)}"
        )
    return names[0]


def _normalize_timestamp_ms(value: str) -> int:
    ts = int(float(value))
    # Binance archives may expose microsecond timestamps in newer datasets.
    # Normalize to milliseconds because the replay/state layer is millisecond based.
    if ts > 100_000_000_000_000:
        ts //= 1_000
    return ts


def _append_zip_csv(zip_path: Path, writer: csv.writer) -> int:
    written = 0
    with zipfile.ZipFile(zip_path) as archive:
        name = _single_csv_name(archive, zip_path.name)
        with archive.open(name, "r") as raw:
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


def _bookticker_columns(header: list[str] | None) -> dict[str, int]:
    if header is None:
        return {
            "update_id": 0,
            "bid": 1,
            "bid_qty": 2,
            "ask": 3,
            "ask_qty": 4,
            "transaction_time": 5,
            "event_time": 6,
        }

    index = {name.strip().lower(): i for i, name in enumerate(header)}

    def find(*aliases: str, required: bool = True) -> int:
        for alias in aliases:
            if alias in index:
                return index[alias]
        if required:
            raise RuntimeError(f"bookTicker archive missing columns: {aliases}")
        return -1

    event_idx = find("event_time", "eventtime", required=False)
    transaction_idx = find("transaction_time", "transactiontime", required=False)
    if event_idx < 0 and transaction_idx < 0:
        raise RuntimeError("bookTicker archive has no event/transaction time column")
    if event_idx < 0:
        event_idx = transaction_idx
    if transaction_idx < 0:
        transaction_idx = event_idx

    return {
        "update_id": find("update_id", "updateid", required=False),
        "bid": find("best_bid_price", "bid_price", "bidprice"),
        "bid_qty": find("best_bid_qty", "bid_qty", "bidqty"),
        "ask": find("best_ask_price", "ask_price", "askprice"),
        "ask_qty": find("best_ask_qty", "ask_qty", "askqty"),
        "transaction_time": transaction_idx,
        "event_time": event_idx,
    }


def _append_bookticker_sampled_zip(
    zip_path: Path,
    writer: csv.writer,
    *,
    bucket_ms: int = 60_000,
) -> int:
    """Keep only the latest bookTicker at-or-before each minute boundary.

    Raw bookTicker archives can be very large. The backtest only needs the latest
    observable snapshot close to each 1m bar close. We therefore keep one row per
    minute bucket, selected by maximum event timestamp. A full-day dictionary is
    bounded to ~1440 entries and is also robust to archive rows arriving unsorted.
    """
    if bucket_ms <= 0:
        raise ValueError("bucket_ms must be positive")

    latest: dict[int, tuple[int, list[str]]] = {}
    with zipfile.ZipFile(zip_path) as archive:
        name = _single_csv_name(archive, zip_path.name)
        with archive.open(name, "r") as raw:
            text = io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
            reader = csv.reader(text)
            first = next(reader, None)
            if first is None:
                return 0

            header: list[str] | None = None
            if _numeric_first(first[0]):
                row_iter = chain((first,), reader)
            else:
                header = first
                row_iter = reader

            columns = _bookticker_columns(header)

            for row in row_iter:
                if not row:
                    continue
                try:
                    event_ms = _normalize_timestamp_ms(row[columns["event_time"]])
                    transaction_ms = _normalize_timestamp_ms(row[columns["transaction_time"]])
                    update_id = (
                        row[columns["update_id"]]
                        if columns["update_id"] >= 0
                        else str(event_ms)
                    )
                    normalized = [
                        update_id,
                        row[columns["bid"]],
                        row[columns["bid_qty"]],
                        row[columns["ask"]],
                        row[columns["ask_qty"]],
                        str(transaction_ms),
                        str(event_ms),
                    ]
                    # Validate numeric market fields before accepting the sample.
                    float(normalized[1]); float(normalized[2])
                    float(normalized[3]); float(normalized[4])
                except (ValueError, TypeError, IndexError):
                    continue

                # Bucket by the minute containing the event; the latest event in
                # that minute is at-or-before that minute's close.
                bucket = event_ms // bucket_ms
                current = latest.get(bucket)
                if current is None or event_ms > current[0]:
                    latest[bucket] = (event_ms, normalized)

    for _, row in sorted(latest.values(), key=lambda item: item[0]):
        writer.writerow(row)
    return len(latest)


def _append_bookdepth_zip(zip_path: Path, writer: csv.writer) -> int:
    """Append Binance bookDepth rows while stripping per-archive headers.

    bookDepth timestamps are human-readable UTC strings, so the generic numeric
    first-column filter used by kline/aggTrade archives cannot be used here.
    """
    written = 0
    with zipfile.ZipFile(zip_path) as archive:
        name = _single_csv_name(archive, zip_path.name)
        with archive.open(name, "r") as raw:
            text = io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
            reader = csv.reader(text)
            first = next(reader, None)
            if first is None:
                return 0
            header = [x.strip().lower() for x in first]
            has_header = "timestamp" in header and "percentage" in header
            rows = reader if has_header else chain((first,), reader)
            for row in rows:
                if len(row) < 4:
                    continue
                try:
                    percentage = int(float(row[1]))
                    depth = float(row[2])
                    notional = float(row[3])
                except (ValueError, TypeError):
                    continue
                if percentage == 0 or abs(percentage) > 5 or depth < 0 or notional < 0:
                    continue
                writer.writerow([row[0].strip(), percentage, depth, notional])
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
                    if dataset == "bookTicker":
                        rows += _append_bookticker_sampled_zip(zip_path, writer)
                    elif dataset == "bookDepth":
                        rows += _append_bookdepth_zip(zip_path, writer)
                    else:
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
        description="Download checksum-verified Binance USD-M scalping datasets; bookTicker is minute-sampled"
    )
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--end-date", help="UTC YYYY-MM-DD; default is yesterday")
    p.add_argument("--output-dir", default="data/scalping")
    p.add_argument(
        "--include-bookticker",
        action="store_true",
        help="Legacy/diagnostic only; current USD-M bookTicker archives may be unavailable",
    )
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
    book_result: dict[str, object] = {
        "status": "NOT_REQUESTED",
        "reason": "current historical gate uses supported kline price-action+taker-flow only",
    }
    if args.include_bookticker:
        b_archives, b_rows = download_dataset(
            dataset="bookTicker",
            symbol=symbol,
            start=start,
            end=end,
            output_csv=book_path,
        )
        book_result = {
            "status": "DOWNLOADED",
            "archives": b_archives,
            "rows": b_rows,
            "path": str(book_path),
        }

    print(
        {
            "symbol": symbol,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "klines": {"archives": k_archives, "rows": k_rows, "path": str(kline_path)},
            "bookTicker": book_result,
        },
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
