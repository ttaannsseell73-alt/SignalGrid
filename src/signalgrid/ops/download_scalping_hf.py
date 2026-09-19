from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path

from signalgrid.backtest.highfreq import (
    aggregate_aggtrades,
    iter_binance_aggtrades_csv,
    write_historical_bars_csv,
)
from signalgrid.ops.download_scalping_data import download_dataset


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Download Binance USD-M aggTrades and build high-frequency scalp bars"
    )
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--days", type=int, default=1)
    p.add_argument("--bucket-ms", type=int, default=5_000)
    p.add_argument("--end-date", help="UTC YYYY-MM-DD; default is yesterday")
    p.add_argument("--output-dir", default="data/scalping-hf")
    args = p.parse_args(argv)

    if args.days < 1 or args.days > 7:
        p.error("--days must be between 1 and 7 for high-frequency research")
    if args.bucket_ms < 1_000 or args.bucket_ms > 60_000:
        p.error("--bucket-ms must be between 1000 and 60000")

    end = (
        datetime.strptime(args.end_date, "%Y-%m-%d").date()
        if args.end_date
        else datetime.now(timezone.utc).date() - timedelta(days=1)
    )
    start = end - timedelta(days=args.days - 1)
    symbol = args.symbol.upper()
    outdir = Path(args.output_dir)
    raw = outdir / f"{symbol}-aggTrades.csv"
    bars_path = outdir / f"{symbol}-{args.bucket_ms}ms.csv"

    archives, rows = download_dataset(
        dataset="aggTrades",
        symbol=symbol,
        start=start,
        end=end,
        output_csv=raw,
    )
    bars = aggregate_aggtrades(
        iter_binance_aggtrades_csv(raw, symbol),
        bucket_ms=args.bucket_ms,
        fill_gaps=True,
    )
    bar_count = write_historical_bars_csv(bars, bars_path)
    if bar_count == 0:
        raise RuntimeError("aggTrades aggregation produced no bars")

    print(
        {
            "symbol": symbol,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "aggTrade_archives": archives,
            "aggTrade_rows": rows,
            "bucket_ms": args.bucket_ms,
            "bars": bar_count,
            "bars_path": str(bars_path),
        },
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
