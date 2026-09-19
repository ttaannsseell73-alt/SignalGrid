from __future__ import annotations

import argparse
import json
from pathlib import Path

from signalgrid.backtest.bookdepth import depth_quality_report, load_bookdepth_csv
from signalgrid.backtest.data import load_binance_klines_csv
from signalgrid.backtest.scalping_bookdepth_research_cli import (
    LOOKBACK,
    MAX_DEPTH_AGE_MS,
    _align_depth,
    _depth_regime,
    _flow_regime,
)
from signalgrid.backtest.scalping_hf_feature_study_cli import _structure_event


FROZEN_BOOKDEPTH_HYPOTHESIS = {
    "event": "BREAKOUT_DOWN",
    "flow": "FLOW_SELL",
    "depth_regime": "BID_STACKED",
    "direction": "LONG",
    "lookback_bars": 20,
    "horizon_bars": 20,
    "bar_seconds": 30,
}
COST_GATES_BPS = (4.0, 8.0, 12.5)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Disjoint validation for frozen bookDepth absorption cell"
    )
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--bars", required=True)
    p.add_argument("--bookdepth", required=True)
    p.add_argument("--min-samples", type=int, default=100)
    p.add_argument("--output", default="validation/bookdepth_disjoint_candidate.json")
    args = p.parse_args(argv)

    symbol = args.symbol.upper()
    bars = load_binance_klines_csv(args.bars, symbol)
    snapshots = load_bookdepth_csv(args.bookdepth, symbol)
    quality = depth_quality_report(snapshots)

    if quality["snapshots"] < 1000:
        raise SystemExit("insufficient bookDepth snapshots")
    if quality["frozen_inner_ask_fraction"] >= 0.95:
        raise SystemExit("bookDepth inner ask appears frozen")
    if quality["frozen_inner_bid_fraction"] >= 0.95:
        raise SystemExit("bookDepth inner bid appears frozen")

    aligned = _align_depth(bars, snapshots)
    coverage = sum(1 for item in aligned if item is not None) / max(1, len(aligned))
    if coverage < 0.95:
        raise SystemExit(f"bookDepth coverage too low: {coverage:.4f}")

    horizon = FROZEN_BOOKDEPTH_HYPOTHESIS["horizon_bars"]
    returns: list[float] = []
    end = len(bars) - horizon - 1
    for i in range(LOOKBACK, end):
        depth = aligned[i]
        if depth is None:
            continue
        if _structure_event(bars, i, LOOKBACK) != FROZEN_BOOKDEPTH_HYPOTHESIS["event"]:
            continue
        if _flow_regime(bars[i]) != FROZEN_BOOKDEPTH_HYPOTHESIS["flow"]:
            continue
        if _depth_regime(depth) != FROZEN_BOOKDEPTH_HYPOTHESIS["depth_regime"]:
            continue
        entry = bars[i + 1].open
        exit_price = bars[i + horizon].close
        if entry <= 0:
            continue
        returns.append((exit_price - entry) / entry * 10_000.0)

    samples = len(returns)
    gross_mean = sum(returns) / samples if samples else 0.0
    hit_rate = sum(1 for x in returns if x > 0) / samples if samples else 0.0
    costs = {
        str(cost): {
            "net_mean_bps": gross_mean - cost,
            "positive": gross_mean - cost > 0,
        }
        for cost in COST_GATES_BPS
    }
    passed = (
        samples >= args.min_samples
        and hit_rate > 0.55
        and costs["8.0"]["positive"]
    )

    payload = {
        "candidate_frozen_before_disjoint_test": True,
        "hypothesis": FROZEN_BOOKDEPTH_HYPOTHESIS,
        "symbol": symbol,
        "samples": samples,
        "min_samples": args.min_samples,
        "gross_mean_bps": gross_mean,
        "gross_hit_rate": hit_rate,
        "cost_results": costs,
        "bookdepth_quality": quality,
        "alignment_coverage": coverage,
        "passed": passed,
        "interpretation": (
            "Fail retires this cell. Pass advances only to broader symbol/execution "
            "validation and does not unlock Demo."
        ),
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
