from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from signalgrid.backtest.data import load_binance_klines_csv
from signalgrid.backtest.scalping_hf_feature_study_cli import _flow_bin, _structure_event, _taker_imbalance


HORIZONS = (12, 36, 60)  # 60s, 180s, 300s on 5s bars


def study_horizon(rows, *, lookback: int, horizon: int, min_samples: int) -> list[dict]:
    cells: dict[tuple[str, str], list[float]] = defaultdict(list)
    end = len(rows) - horizon - 1
    for i in range(lookback, end):
        cur = rows[i]
        entry = rows[i + 1].open
        exit_price = rows[i + horizon].close
        if entry <= 0:
            continue
        forward_bps = (exit_price - entry) / entry * 10_000.0
        key = (
            _structure_event(rows, i, lookback),
            _flow_bin(_taker_imbalance(cur)),
        )
        cells[key].append(forward_bps)

    results = []
    for (event, flow), values in cells.items():
        if len(values) < min_samples:
            continue
        mean_long = sum(values) / len(values)
        long_hit = sum(1 for value in values if value > 0) / len(values)
        direction = "LONG" if mean_long >= 0 else "SHORT"
        gross = abs(mean_long)
        results.append(
            {
                "event": event,
                "flow_bin": flow,
                "samples": len(values),
                "preferred_direction": direction,
                "gross_forward_mean_bps": gross,
                "gross_hit_rate": long_hit if direction == "LONG" else 1.0 - long_hit,
                "net_mean_after_4bps": gross - 4.0,
                "net_mean_after_8bps": gross - 8.0,
                "net_mean_after_12_5bps": gross - 12.5,
            }
        )
    return sorted(
        results,
        key=lambda x: (x["net_mean_after_4bps"], x["samples"]),
        reverse=True,
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Multi-horizon 5s PA x flow edge study")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--bars", required=True)
    p.add_argument("--lookback", type=int, default=24)
    p.add_argument("--min-samples", type=int, default=30)
    p.add_argument("--output", default="validation/scalping_hf_multihorizon_features.json")
    args = p.parse_args(argv)

    rows = load_binance_klines_csv(args.bars, args.symbol.upper())
    horizons = {
        str(h * 5): study_horizon(
            rows,
            lookback=args.lookback,
            horizon=h,
            min_samples=args.min_samples,
        )
        for h in HORIZONS
    }
    payload = {
        "research_only": True,
        "symbol": args.symbol.upper(),
        "timeframe_ms": 5_000,
        "lookback_bars": args.lookback,
        "horizon_seconds": [h * 5 for h in HORIZONS],
        "warning": (
            "Exploration-only. Any discovered cell must be frozen before testing "
            "on a disjoint historical period."
        ),
        "horizons": horizons,
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
