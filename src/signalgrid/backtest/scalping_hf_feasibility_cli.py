from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import median

from signalgrid.backtest.data import load_binance_klines_csv


HORIZON_BARS = (3, 6, 12)  # 15s, 30s, 60s on 5s data
BASE_ROUND_TRIP_COST_BPS = 12.5
STRESS_ROUND_TRIP_COST_BPS = 23.0


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * q
    lo = int(pos)
    hi = min(len(ordered) - 1, lo + 1)
    frac = pos - lo
    return ordered[lo] * (1.0 - frac) + ordered[hi] * frac


def study(rows, horizon: int) -> dict:
    abs_close_moves: list[float] = []
    max_excursions: list[float] = []
    directional_best: list[float] = []

    # Decision occurs on bar i close; realistic entry is next bar open.
    for i in range(len(rows) - horizon - 1):
        entry = rows[i + 1].open
        if entry <= 0:
            continue
        future = rows[i + 1:i + horizon + 1]
        exit_close = future[-1].close
        close_move = (exit_close - entry) / entry * 10_000.0
        up_mfe = (max(b.high for b in future) - entry) / entry * 10_000.0
        down_mfe = (entry - min(b.low for b in future)) / entry * 10_000.0
        abs_close_moves.append(abs(close_move))
        max_excursions.append(max(up_mfe, down_mfe))
        directional_best.append(max(close_move, -close_move))

    n = len(max_excursions)
    return {
        "samples": n,
        "median_abs_close_move_bps": median(abs_close_moves) if abs_close_moves else 0.0,
        "p75_abs_close_move_bps": _percentile(abs_close_moves, 0.75),
        "p90_abs_close_move_bps": _percentile(abs_close_moves, 0.90),
        "median_max_excursion_bps": median(max_excursions) if max_excursions else 0.0,
        "p75_max_excursion_bps": _percentile(max_excursions, 0.75),
        "p90_max_excursion_bps": _percentile(max_excursions, 0.90),
        "base_cost_reachable_fraction": (
            sum(1 for x in max_excursions if x >= BASE_ROUND_TRIP_COST_BPS) / n
            if n else 0.0
        ),
        "stress_cost_reachable_fraction": (
            sum(1 for x in max_excursions if x >= STRESS_ROUND_TRIP_COST_BPS) / n
            if n else 0.0
        ),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="SignalGrid high-frequency cost feasibility study")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--bars", required=True)
    p.add_argument("--bucket-ms", type=int, default=5_000)
    p.add_argument("--output", default="validation/scalping_hf_feasibility.json")
    args = p.parse_args(argv)

    rows = load_binance_klines_csv(args.bars, args.symbol.upper())
    payload = {
        "research_only": True,
        "symbol": args.symbol.upper(),
        "bucket_ms": args.bucket_ms,
        "base_round_trip_cost_bps": BASE_ROUND_TRIP_COST_BPS,
        "stress_round_trip_cost_bps": STRESS_ROUND_TRIP_COST_BPS,
        "interpretation": (
            "This measures whether raw short-horizon BTC movement is large enough "
            "to clear modeled taker execution costs before any signal selection."
        ),
        "horizons": {
            str(h * args.bucket_ms): study(rows, h)
            for h in HORIZON_BARS
        },
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
