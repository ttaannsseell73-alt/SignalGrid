from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from signalgrid.backtest.data import load_binance_klines_csv


FLOW_BINS = (
    (-1.01, -0.30, "FLOW_STRONG_SELL"),
    (-0.30, -0.10, "FLOW_SELL"),
    (-0.10, 0.10, "FLOW_BALANCED"),
    (0.10, 0.30, "FLOW_BUY"),
    (0.30, 1.01, "FLOW_STRONG_BUY"),
)


def _flow_bin(value: float) -> str:
    for lo, hi, name in FLOW_BINS:
        if lo <= value < hi:
            return name
    return "FLOW_OUTSIDE"


def _taker_imbalance(bar) -> float:
    total = bar.quote_volume
    if total <= 0:
        return 0.0
    sell = max(0.0, total - bar.taker_buy_quote)
    return (bar.taker_buy_quote - sell) / total


def _structure_event(rows, i: int, lookback: int) -> str:
    cur = rows[i]
    prior = rows[i - lookback:i]
    hi = max(x.high for x in prior)
    lo = min(x.low for x in prior)
    if cur.low < lo and cur.close > lo:
        return "SWEEP_LOW_RECLAIM"
    if cur.high > hi and cur.close < hi:
        return "SWEEP_HIGH_REJECT"
    if cur.close > hi:
        return "BREAKOUT_UP"
    if cur.close < lo:
        return "BREAKOUT_DOWN"
    return "INSIDE_RANGE"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Exploratory 5s PA x taker-flow conditional edge study")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--bars", required=True)
    p.add_argument("--lookback", type=int, default=24)
    p.add_argument("--horizon-bars", type=int, default=12)
    p.add_argument("--min-samples", type=int, default=30)
    p.add_argument("--output", default="validation/scalping_hf_feature_study.json")
    args = p.parse_args(argv)

    rows = load_binance_klines_csv(args.bars, args.symbol.upper())
    cells: dict[tuple[str, str], list[float]] = defaultdict(list)

    end = len(rows) - args.horizon_bars - 1
    for i in range(args.lookback, end):
        cur = rows[i]
        entry = rows[i + 1].open
        exit_price = rows[i + args.horizon_bars].close
        if entry <= 0:
            continue
        forward_bps = (exit_price - entry) / entry * 10_000.0
        event = _structure_event(rows, i, args.lookback)
        flow = _flow_bin(_taker_imbalance(cur))
        cells[(event, flow)].append(forward_bps)

    results = []
    for (event, flow), values in cells.items():
        if len(values) < args.min_samples:
            continue
        mean_long = sum(values) / len(values)
        wins_long = sum(1 for x in values if x > 0) / len(values)
        direction = "LONG" if mean_long >= 0 else "SHORT"
        directional_mean = abs(mean_long)
        directional_hit = wins_long if direction == "LONG" else 1.0 - wins_long
        results.append(
            {
                "event": event,
                "flow_bin": flow,
                "samples": len(values),
                "preferred_direction": direction,
                "gross_forward_mean_bps": directional_mean,
                "gross_hit_rate": directional_hit,
                "net_mean_after_4bps": directional_mean - 4.0,
                "net_mean_after_8bps": directional_mean - 8.0,
                "net_mean_after_12_5bps": directional_mean - 12.5,
            }
        )

    results.sort(
        key=lambda x: (
            x["net_mean_after_4bps"],
            x["samples"],
        ),
        reverse=True,
    )
    payload = {
        "research_only": True,
        "symbol": args.symbol.upper(),
        "timeframe_ms": 5_000,
        "lookback_bars": args.lookback,
        "forward_horizon_bars": args.horizon_bars,
        "forward_horizon_seconds": args.horizon_bars * 5,
        "min_samples": args.min_samples,
        "warning": (
            "This is exploratory conditional discovery on already-used data. "
            "Any interesting cell must be frozen and validated on disjoint data."
        ),
        "cells": results,
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
