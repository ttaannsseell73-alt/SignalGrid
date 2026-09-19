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


def _bookticker_event_index(header: list[str] | None) -> int:
    if header is None:
        # Canonical Binance USD-M archive layout:
        # update_id,bid_price,bid_qty,ask_price,ask_qty,transaction_time,event_time
        return 6
    index = {name.strip().lower(): i for i, name in enumerate(header)}
    for alias in ("event_time", "eventtime", "transaction_time", "transactiontime"):
        if alias in index:
            return index[alias]
    raise RuntimeError("bookTicker archive has no event/transaction time column")


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
            rows = []
            if _numeric_first(first[0]):
                rows.append(first)
            else:
                header = first

            time_index = _bookticker_event_index(header)
            rows.extend(reader)

            for row in rows:
                if not row or time_index >= len(row) or not _numeric_first(row[0]):
                    continue
                try:
                    event_ms = _normalize_timestamp_ms(row[time_index])
                except (ValueError, TypeError):
                    continue
                # Bucket by the minute containing the event; the latest event in
                # that minute is at-or-before that minute's close.
                bucket = event_ms // bucket_ms
                current = latest.get(bucket)
                if current is None or event_ms > current[0]:
                    normalized = list(row)
                    normalized[time_index] = str(event_ms)
                    # Canonical headerless layout also carries transaction time.
                    if header is None and len(normalized) > 5:
                        try:
                            normalized[5] = str(_normalize_timestamp_ms(normalized[5]))
                        except (ValueError, TypeError):
                            pass
                    latest[bucket] = (event_ms, normalized)

    for _, row in sorted(latest.values(), key=lambda item: item[0]):
        writer.writerow(row)
    return len(latest)


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
