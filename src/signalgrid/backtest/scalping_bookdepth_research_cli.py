from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from signalgrid.backtest.bookdepth import (
    BookDepthSnapshot,
    depth_quality_report,
    load_bookdepth_csv,
)
from signalgrid.backtest.data import HistoricalBar, load_binance_klines_csv
from signalgrid.backtest.scalping_hf_feature_study_cli import _structure_event, _taker_imbalance


HORIZONS = (10, 20)  # 5m, 10m on 30s bars
LOOKBACK = 20  # 10m structure context
MAX_DEPTH_AGE_MS = 35_000


def _flow_regime(bar: HistoricalBar) -> str:
    value = _taker_imbalance(bar)
    if value >= 0.10:
        return "FLOW_BUY"
    if value <= -0.10:
        return "FLOW_SELL"
    return "FLOW_BALANCED"


def _depth_regime(snapshot: BookDepthSnapshot) -> str:
    inner = snapshot.depth_imbalance_1
    outer = snapshot.depth_imbalance_5
    if inner >= 0.10 and outer >= 0.05:
        return "BID_STACKED"
    if inner <= -0.10 and outer <= -0.05:
        return "ASK_STACKED"
    if inner >= 0.10:
        return "INNER_BID"
    if inner <= -0.10:
        return "INNER_ASK"
    return "DEPTH_BALANCED"


def _align_depth(
    bars: list[HistoricalBar],
    snapshots: list[BookDepthSnapshot],
) -> list[BookDepthSnapshot | None]:
    out: list[BookDepthSnapshot | None] = []
    j = 0
    latest: BookDepthSnapshot | None = None
    for bar in bars:
        while j < len(snapshots) and snapshots[j].timestamp_ms <= bar.close_time_ms:
            latest = snapshots[j]
            j += 1
        if latest is None or bar.close_time_ms - latest.timestamp_ms > MAX_DEPTH_AGE_MS:
            out.append(None)
        else:
            out.append(latest)
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="30s Binance bookDepth + aggTrades exploratory microstructure study"
    )
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--bars", required=True)
    p.add_argument("--bookdepth", required=True)
    p.add_argument("--min-samples", type=int, default=20)
    p.add_argument("--output", default="validation/bookdepth_microstructure_research.json")
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

    horizon_results = {}
    for horizon in HORIZONS:
        cells: dict[tuple[str, str, str], list[float]] = defaultdict(list)
        end = len(bars) - horizon - 1
        for i in range(LOOKBACK, end):
            depth = aligned[i]
            if depth is None:
                continue
            entry = bars[i + 1].open
            exit_price = bars[i + horizon].close
            if entry <= 0:
                continue
            forward_bps = (exit_price - entry) / entry * 10_000.0
            key = (
                _structure_event(bars, i, LOOKBACK),
                _flow_regime(bars[i]),
                _depth_regime(depth),
            )
            cells[key].append(forward_bps)

        rows = []
        for (event, flow, depth_regime), values in cells.items():
            if len(values) < args.min_samples:
                continue
            raw_mean = sum(values) / len(values)
            direction = "LONG" if raw_mean >= 0 else "SHORT"
            signed = values if direction == "LONG" else [-x for x in values]
            mean_bps = sum(signed) / len(signed)
            hit_rate = sum(1 for x in signed if x > 0) / len(signed)
            rows.append(
                {
                    "event": event,
                    "flow": flow,
                    "depth_regime": depth_regime,
                    "samples": len(values),
                    "preferred_direction": direction,
                    "gross_mean_bps": mean_bps,
                    "hit_rate": hit_rate,
                    "net_after_4bps": mean_bps - 4.0,
                    "net_after_8bps": mean_bps - 8.0,
                    "net_after_12_5bps": mean_bps - 12.5,
                }
            )
        rows.sort(key=lambda x: (x["net_after_8bps"], x["samples"]), reverse=True)
        horizon_results[str(horizon * 30)] = rows

    payload = {
        "research_only": True,
        "symbol": symbol,
        "bar_seconds": 30,
        "bookdepth_sampling": "approximately 30s historical snapshots",
        "bookdepth_quality": quality,
        "alignment_coverage": coverage,
        "warning": (
            "Exploratory only. Historical Binance bookDepth has known data-quality "
            "reports, so relative imbalance is used only behind explicit quality gates. "
            "Any discovered cell requires disjoint validation."
        ),
        "horizons": horizon_results,
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
