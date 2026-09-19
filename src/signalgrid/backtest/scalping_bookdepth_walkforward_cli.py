from __future__ import annotations

import argparse
import json
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path

from signalgrid.backtest.bookdepth import depth_quality_report, load_bookdepth_csv
from signalgrid.backtest.data import HistoricalBar, load_binance_klines_csv
from signalgrid.backtest.scalping_bookdepth_research_cli import (
    LOOKBACK,
    _align_depth,
    _depth_regime,
    _flow_regime,
)
from signalgrid.backtest.scalping_hf_feature_study_cli import _structure_event


TRAIN_DAYS = 3
VALIDATION_DAYS = 2
TEST_DAYS = 2
HORIZON_BARS = 20  # 10 minutes on 30s bars
MIN_TRAIN_SAMPLES = 30
MIN_HOLDOUT_SAMPLES = 15


@dataclass(frozen=True, slots=True)
class DepthCell:
    event: str
    flow: str
    depth_regime: str


def _split_boundaries(bars: list[HistoricalBar]) -> tuple[int, int]:
    start = bars[0].open_time_ms
    day_ms = 86_400_000
    return (
        start + TRAIN_DAYS * day_ms,
        start + (TRAIN_DAYS + VALIDATION_DAYS) * day_ms,
    )


def _stats(values: list[float], direction: str) -> dict:
    signed = values if direction == "LONG" else [-x for x in values]
    n = len(signed)
    mean_bps = sum(signed) / n if n else 0.0
    return {
        "samples": n,
        "directional_mean_bps": mean_bps,
        "hit_rate": sum(1 for x in signed if x > 0) / n if n else 0.0,
        "net_after_4bps": mean_bps - 4.0,
        "net_after_8bps": mean_bps - 8.0,
        "net_after_12_5bps": mean_bps - 12.5,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="7-day walk-forward study for PA + taker-flow + bookDepth"
    )
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--bars", required=True)
    p.add_argument("--bookdepth", required=True)
    p.add_argument("--output", default="validation/bookdepth_walkforward.json")
    args = p.parse_args(argv)

    symbol = args.symbol.upper()
    bars = load_binance_klines_csv(args.bars, symbol)
    depth = load_bookdepth_csv(args.bookdepth, symbol)
    quality = depth_quality_report(depth)
    if quality["frozen_inner_ask_fraction"] >= 0.95 or quality["frozen_inner_bid_fraction"] >= 0.95:
        raise SystemExit("bookDepth frozen-side quality gate failed")

    aligned = _align_depth(bars, depth)
    coverage = sum(x is not None for x in aligned) / max(1, len(aligned))
    if coverage < 0.95:
        raise SystemExit(f"bookDepth coverage too low: {coverage:.4f}")

    validation_start, test_start = _split_boundaries(bars)
    train: dict[DepthCell, list[float]] = defaultdict(list)
    validation: dict[DepthCell, list[float]] = defaultdict(list)
    test: dict[DepthCell, list[float]] = defaultdict(list)

    for i in range(LOOKBACK, len(bars) - HORIZON_BARS - 1):
        snap = aligned[i]
        if snap is None:
            continue
        entry = bars[i + 1].open
        exit_price = bars[i + HORIZON_BARS].close
        if entry <= 0:
            continue
        forward_bps = (exit_price - entry) / entry * 10_000.0
        cell = DepthCell(
            event=_structure_event(bars, i, LOOKBACK),
            flow=_flow_regime(bars[i]),
            depth_regime=_depth_regime(snap),
        )
        ts = bars[i].open_time_ms
        if ts < validation_start:
            train[cell].append(forward_bps)
        elif ts < test_start:
            validation[cell].append(forward_bps)
        else:
            test[cell].append(forward_bps)

    candidates = []
    for cell, values in train.items():
        if len(values) < MIN_TRAIN_SAMPLES:
            continue
        raw_mean = sum(values) / len(values)
        direction = "LONG" if raw_mean >= 0 else "SHORT"
        train_stats = _stats(values, direction)
        if train_stats["net_after_8bps"] <= 0:
            continue

        val_stats = _stats(validation.get(cell, []), direction)
        test_stats = _stats(test.get(cell, []), direction)
        validation_pass = (
            val_stats["samples"] >= MIN_HOLDOUT_SAMPLES
            and val_stats["net_after_4bps"] > 0
            and val_stats["hit_rate"] > 0.50
        )
        test_pass = (
            test_stats["samples"] >= MIN_HOLDOUT_SAMPLES
            and test_stats["net_after_4bps"] > 0
            and test_stats["hit_rate"] > 0.50
        )
        candidates.append(
            {
                "cell": asdict(cell),
                "direction": direction,
                "train": train_stats,
                "validation": val_stats,
                "test": test_stats,
                "validation_pass": validation_pass,
                "test_pass": test_pass,
                "walkforward_pass": validation_pass and test_pass,
            }
        )

    candidates.sort(
        key=lambda x: (
            x["walkforward_pass"],
            x["test"]["net_after_4bps"],
            x["validation"]["net_after_4bps"],
            x["train"]["net_after_8bps"],
        ),
        reverse=True,
    )

    payload = {
        "research_only": True,
        "symbol": symbol,
        "bar_seconds": 30,
        "horizon_seconds": HORIZON_BARS * 30,
        "split_days": {
            "train": TRAIN_DAYS,
            "validation": VALIDATION_DAYS,
            "test": TEST_DAYS,
        },
        "bookdepth_quality": quality,
        "alignment_coverage": coverage,
        "candidate_count": len(candidates),
        "walkforward_pass_count": sum(1 for x in candidates if x["walkforward_pass"]),
        "selection_rule": (
            "Train >8 bps net hurdle; validation and test each >4 bps net, "
            ">50% hit rate and >=15 samples."
        ),
        "candidates": candidates,
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
