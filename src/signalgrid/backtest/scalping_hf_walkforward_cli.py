from __future__ import annotations

import argparse
import json
from collections import defaultdict
from dataclasses import dataclass, asdict
from pathlib import Path

from signalgrid.backtest.data import HistoricalBar, load_binance_klines_csv
from signalgrid.backtest.scalping_hf_feature_study_cli import (
    _flow_bin,
    _structure_event,
    _taker_imbalance,
)


TRAIN_DAYS = 3
VALIDATION_DAYS = 2
TEST_DAYS = 2
HORIZON_BARS = 60  # 5 minutes on 5s bars
CUM_FLOW_BARS = 6  # 30 seconds
MOMENTUM_BARS = 12  # 60 seconds
MIN_TRAIN_SAMPLES = 50
MIN_HOLDOUT_SAMPLES = 20


@dataclass(frozen=True, slots=True)
class Cell:
    event: str
    flow: str
    cumulative_flow: str
    momentum: str


def _cum_flow(rows: list[HistoricalBar], start: int, end: int) -> float:
    buy = sum(max(0.0, b.taker_buy_quote) for b in rows[start:end])
    total = sum(max(0.0, b.quote_volume) for b in rows[start:end])
    if total <= 0:
        return 0.0
    sell = max(0.0, total - buy)
    return (buy - sell) / total


def _momentum_bin(rows: list[HistoricalBar], i: int) -> str:
    base = rows[i - MOMENTUM_BARS].close
    if base <= 0:
        return "MOM_FLAT"
    move_bps = (rows[i].close - base) / base * 10_000.0
    if move_bps >= 5.0:
        return "MOM_UP"
    if move_bps <= -5.0:
        return "MOM_DOWN"
    return "MOM_FLAT"


def _cell(rows: list[HistoricalBar], i: int, lookback: int) -> Cell:
    return Cell(
        event=_structure_event(rows, i, lookback),
        flow=_flow_bin(_taker_imbalance(rows[i])),
        cumulative_flow=_flow_bin(
            _cum_flow(rows, i - CUM_FLOW_BARS + 1, i + 1)
        ),
        momentum=_momentum_bin(rows, i),
    )


def _split_boundaries(rows: list[HistoricalBar]) -> tuple[int, int]:
    if not rows:
        return 0, 0
    start = rows[0].open_time_ms
    day_ms = 86_400_000
    validation_start = start + TRAIN_DAYS * day_ms
    test_start = validation_start + VALIDATION_DAYS * day_ms
    return validation_start, test_start


def _bucket(
    rows: list[HistoricalBar],
    *,
    lookback: int,
) -> tuple[
    dict[Cell, list[float]],
    dict[Cell, list[float]],
    dict[Cell, list[float]],
]:
    train: dict[Cell, list[float]] = defaultdict(list)
    validation: dict[Cell, list[float]] = defaultdict(list)
    test: dict[Cell, list[float]] = defaultdict(list)
    validation_start, test_start = _split_boundaries(rows)
    warmup = max(lookback, CUM_FLOW_BARS, MOMENTUM_BARS)

    for i in range(warmup, len(rows) - HORIZON_BARS - 1):
        entry = rows[i + 1].open
        exit_price = rows[i + HORIZON_BARS].close
        if entry <= 0:
            continue
        forward_bps = (exit_price - entry) / entry * 10_000.0
        key = _cell(rows, i, lookback)
        ts = rows[i].open_time_ms
        if ts < validation_start:
            train[key].append(forward_bps)
        elif ts < test_start:
            validation[key].append(forward_bps)
        else:
            test[key].append(forward_bps)
    return train, validation, test


def _stats(values: list[float], direction: str) -> dict:
    signed = values if direction == "LONG" else [-x for x in values]
    n = len(signed)
    mean_bps = sum(signed) / n if n else 0.0
    hit = sum(1 for x in signed if x > 0) / n if n else 0.0
    return {
        "samples": n,
        "directional_mean_bps": mean_bps,
        "hit_rate": hit,
        "net_after_4bps": mean_bps - 4.0,
        "net_after_8bps": mean_bps - 8.0,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="7-day walk-forward 5s PA x flow feature-family study"
    )
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--bars", required=True)
    p.add_argument("--lookback", type=int, default=24)
    p.add_argument("--output", default="validation/scalping_hf_walkforward.json")
    args = p.parse_args(argv)

    rows = load_binance_klines_csv(args.bars, args.symbol.upper())
    expected_span_ms = (TRAIN_DAYS + VALIDATION_DAYS + TEST_DAYS) * 86_400_000
    actual_span_ms = rows[-1].close_time_ms - rows[0].open_time_ms if rows else 0
    if actual_span_ms < expected_span_ms - 60_000:
        raise SystemExit("walk-forward study requires a full 7-day dataset")

    train, validation, test = _bucket(rows, lookback=args.lookback)

    candidates = []
    for key, train_values in train.items():
        if len(train_values) < MIN_TRAIN_SAMPLES:
            continue
        train_mean = sum(train_values) / len(train_values)
        direction = "LONG" if train_mean >= 0 else "SHORT"
        train_stats = _stats(train_values, direction)
        if train_stats["net_after_8bps"] <= 0:
            continue

        val_stats = _stats(validation.get(key, []), direction)
        test_stats = _stats(test.get(key, []), direction)
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
                "cell": asdict(key),
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
        "symbol": args.symbol.upper(),
        "timeframe_ms": 5_000,
        "horizon_seconds": HORIZON_BARS * 5,
        "split_days": {
            "train": TRAIN_DAYS,
            "validation": VALIDATION_DAYS,
            "test": TEST_DAYS,
        },
        "feature_family": [
            "structure_event",
            "current_taker_flow_bin",
            "30s_cumulative_taker_flow_bin",
            "60s_price_momentum_bin",
        ],
        "selection_rule": (
            "Train cell must exceed 8 bps directional mean after cost hurdle; "
            "validation and test must each retain >4 bps net mean, >50% hit rate, "
            "and at least 20 samples."
        ),
        "candidate_count": len(candidates),
        "walkforward_pass_count": sum(1 for x in candidates if x["walkforward_pass"]),
        "candidates": candidates,
    }

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
