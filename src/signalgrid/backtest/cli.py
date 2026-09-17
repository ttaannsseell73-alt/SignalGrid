from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from signalgrid.backtest.data import attach_book_snapshots, load_binance_klines_csv, load_book_ticker_csv, load_funding_rate_csv
from signalgrid.backtest.engine import BacktestConfig, parameter_stability, run_backtest
from signalgrid.backtest.walkforward import WalkForwardConfig, run_walk_forward
from signalgrid.signals.engine import SignalConfig


def _candidate_neighborhood(base: SignalConfig) -> list[SignalConfig]:
    out: list[SignalConfig] = []
    for threshold_delta in (-0.05, 0.0, 0.05):
        for expansion_delta in (-0.05, 0.0, 0.05):
            out.append(SignalConfig(
                structure_lookback=base.structure_lookback,
                max_spread_bps=base.max_spread_bps,
                min_expansion=max(0.01, base.min_expansion + expansion_delta),
                strong_expansion=base.strong_expansion,
                min_flow_abs=base.min_flow_abs,
                min_book_abs=base.min_book_abs,
                entry_threshold=min(0.99, max(0.01, base.entry_threshold + threshold_delta)),
                ttl_seconds=base.ttl_seconds,
            ))
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="SignalGrid M5 offline no-lookahead backtest/walk-forward harness")
    p.add_argument("--symbol", required=True)
    p.add_argument("--klines", required=True, help="Binance USD-M kline CSV")
    p.add_argument("--bookticker", required=True, help="Binance USD-M bookTicker CSV")
    p.add_argument("--funding", help="Optional Binance fundingRate CSV")
    p.add_argument("--mode", choices=("backtest", "walk-forward"), default="walk-forward")
    p.add_argument("--book-max-age-ms", type=int, default=2_000)
    p.add_argument("--fee-bps", type=float, default=5.0)
    p.add_argument("--spread-bps", type=float, default=2.0)
    p.add_argument("--slippage-bps", type=float, default=1.0)
    p.add_argument("--notional", type=float, default=1_000.0)
    p.add_argument("--hold-bars", type=int, default=12)
    p.add_argument("--train-bars", type=int, default=2_000)
    p.add_argument("--test-bars", type=int, default=500)
    p.add_argument("--step-bars", type=int, default=500)
    args = p.parse_args(argv)

    bars = load_binance_klines_csv(args.klines, args.symbol)
    books = load_book_ticker_csv(args.bookticker, args.symbol)
    bars = attach_book_snapshots(bars, books, max_age_ms=args.book_max_age_ms)
    missing = sum(1 for b in bars if not b.has_book)
    if missing:
        raise SystemExit(f"book coverage incomplete: {missing}/{len(bars)} bars lack an at-or-before snapshot within {args.book_max_age_ms} ms")
    funding = load_funding_rate_csv(args.funding, args.symbol) if args.funding else []
    costs = BacktestConfig(
        notional_usdt=args.notional,
        taker_fee_bps=args.fee_bps,
        assumed_spread_bps=args.spread_bps,
        slippage_bps=args.slippage_bps,
        max_holding_bars=args.hold_bars,
        require_book=True,
    )
    base = SignalConfig()
    candidates = _candidate_neighborhood(base)
    stability = parameter_stability(bars, candidates, backtest_config=costs, funding_points=funding)
    if args.mode == "backtest":
        result = run_backtest(bars, signal_config=base, backtest_config=costs, funding_points=funding)
        payload = {"mode": args.mode, "symbol": args.symbol.upper(), "metrics": asdict(result.metrics), "stability": asdict(stability)}
    else:
        wf = run_walk_forward(
            bars,
            candidates,
            backtest_config=costs,
            walk_config=WalkForwardConfig(train_bars=args.train_bars, test_bars=args.test_bars, step_bars=args.step_bars),
            funding_points=funding,
        )
        payload = {
            "mode": args.mode,
            "symbol": args.symbol.upper(),
            "folds": len(wf.folds),
            "oos_metrics": asdict(wf.oos_metrics),
            "stability": asdict(stability),
        }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
