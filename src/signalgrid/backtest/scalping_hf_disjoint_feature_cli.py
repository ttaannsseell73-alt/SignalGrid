from __future__ import annotations

import argparse
import json
from pathlib import Path

from signalgrid.backtest.data import load_binance_klines_csv
from signalgrid.backtest.scalping_hf_feature_study_cli import (
    _flow_bin,
    _structure_event,
    _taker_imbalance,
)


FROZEN_HYPOTHESIS = {
    "event": "SWEEP_HIGH_REJECT",
    "flow_bin": "FLOW_STRONG_BUY",
    "direction": "LONG",
    "lookback_bars": 24,
    "horizon_bars": 60,
    "bucket_ms": 5_000,
}

COST_GATES_BPS = (4.0, 8.0, 12.5)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Disjoint validation for frozen 5s PA x flow hypothesis"
    )
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--bars", required=True)
    p.add_argument("--min-samples", type=int, default=100)
    p.add_argument("--output", default="validation/scalping_hf_disjoint_feature.json")
    args = p.parse_args(argv)

    symbol = args.symbol.upper()
    rows = load_binance_klines_csv(args.bars, symbol)
    lookback = FROZEN_HYPOTHESIS["lookback_bars"]
    horizon = FROZEN_HYPOTHESIS["horizon_bars"]

    returns: list[float] = []
    end = len(rows) - horizon - 1
    for i in range(lookback, end):
        cur = rows[i]
        event = _structure_event(rows, i, lookback)
        flow = _flow_bin(_taker_imbalance(cur))
        if event != FROZEN_HYPOTHESIS["event"]:
            continue
        if flow != FROZEN_HYPOTHESIS["flow_bin"]:
            continue
        entry = rows[i + 1].open
        exit_price = rows[i + horizon].close
        if entry <= 0:
            continue
        returns.append((exit_price - entry) / entry * 10_000.0)

    samples = len(returns)
    gross_mean = sum(returns) / samples if samples else 0.0
    hit_rate = sum(1 for value in returns if value > 0) / samples if samples else 0.0
    cost_results = {
        str(cost): {
            "net_mean_bps": gross_mean - cost,
            "positive": gross_mean - cost > 0,
        }
        for cost in COST_GATES_BPS
    }

    # 8 bps is the minimum promotion hurdle for this research family. It is
    # deliberately stricter than the exploratory 4 bps view.
    passed = (
        samples >= args.min_samples
        and hit_rate > 0.50
        and cost_results["8.0"]["positive"]
    )

    payload = {
        "candidate_frozen_before_disjoint_test": True,
        "hypothesis": FROZEN_HYPOTHESIS,
        "symbol": symbol,
        "samples": samples,
        "min_samples": args.min_samples,
        "gross_mean_bps": gross_mean,
        "gross_hit_rate": hit_rate,
        "cost_results": cost_results,
        "passed": passed,
        "interpretation": (
            "A fail retires this discovered cell. A pass only advances it to "
            "multi-symbol and execution-fill validation; it does not unlock Demo."
        ),
    }

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
