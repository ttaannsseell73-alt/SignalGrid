from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from signalgrid.backtest.data import load_binance_klines_csv
from signalgrid.backtest.engine import BacktestConfig, run_backtest
from signalgrid.signals.scalping import ScalpingConfig, ScalpingSignalEngine


HF_PROFILES = {
    "HF_60S": ScalpingConfig(
        structure_lookback=12,
        swing_window=6,
        compression_lookback=12,
        compression_baseline=36,
        compression_ratio_max=0.78,
        retest_tolerance_bps=3.0,
        require_book_microstructure=False,
        min_natr_bps=0.5,
        max_natr_bps=40.0,
        min_expansion=0.90,
        strong_expansion=1.40,
        min_directional_flow=0.08,
        min_score=0.70,
        min_stop_bps=1.5,
        max_stop_bps=40.0,
        ttl_seconds=3.0,
    ),
    "HF_120S": ScalpingConfig(
        structure_lookback=24,
        swing_window=12,
        compression_lookback=18,
        compression_baseline=48,
        compression_ratio_max=0.78,
        retest_tolerance_bps=4.0,
        require_book_microstructure=False,
        min_natr_bps=0.5,
        max_natr_bps=40.0,
        min_expansion=0.90,
        strong_expansion=1.40,
        min_directional_flow=0.10,
        min_score=0.72,
        min_stop_bps=2.0,
        max_stop_bps=50.0,
        ttl_seconds=3.0,
    ),
    "HF_180S": ScalpingConfig(
        structure_lookback=36,
        swing_window=18,
        compression_lookback=24,
        compression_baseline=60,
        compression_ratio_max=0.78,
        retest_tolerance_bps=5.0,
        require_book_microstructure=False,
        min_natr_bps=0.5,
        max_natr_bps=40.0,
        min_expansion=0.90,
        strong_expansion=1.40,
        min_directional_flow=0.12,
        min_score=0.74,
        min_stop_bps=2.5,
        max_stop_bps=60.0,
        ttl_seconds=3.0,
    ),
}


def _bt(stress: bool) -> BacktestConfig:
    return BacktestConfig(
        starting_equity=10_000.0,
        notional_usdt=100.0,
        taker_fee_bps=7.0 if stress else 5.0,
        assumed_spread_bps=3.0 if stress else 1.0,
        slippage_bps=3.0 if stress else 0.75,
        max_holding_bars=12,  # 60 seconds on 5s bars
        require_book=False,
        take_profit_enabled=True,
        min_take_profit_bps=20.0,
        tp_reward_risk_multiple=2.0,
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="SignalGrid 5-second aggTrades scalp research")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--bars", required=True)
    p.add_argument("--output", default="validation/scalping_hf_research.json")
    args = p.parse_args(argv)

    symbol = args.symbol.upper()
    rows = load_binance_klines_csv(args.bars, symbol)
    if len(rows) < 1_000:
        raise SystemExit("high-frequency research requires at least 1000 bars")

    split = max(1, min(len(rows) - 1, int(len(rows) * 0.70)))
    oos_start_ms = rows[split].open_time_ms
    results = {}

    for name, cfg in HF_PROFILES.items():
        base = run_backtest(
            rows,
            backtest_config=_bt(False),
            engine=ScalpingSignalEngine(cfg),
        )
        stress = run_backtest(
            rows,
            backtest_config=_bt(True),
            engine=ScalpingSignalEngine(cfg),
        )
        oos_stress = run_backtest(
            rows,
            backtest_config=_bt(True),
            engine=ScalpingSignalEngine(cfg),
            trade_start_ms=oos_start_ms,
        )
        results[name] = {
            "signal_config": asdict(cfg),
            "base": asdict(base.metrics),
            "stress": asdict(stress.metrics),
            "oos_stress": asdict(oos_stress.metrics),
            "exit_reasons": {
                reason: {
                    "trades": sum(1 for t in base.trades if t.exit_reason == reason),
                    "expectancy": (
                        sum(t.net_pnl for t in base.trades if t.exit_reason == reason)
                        / max(1, sum(1 for t in base.trades if t.exit_reason == reason))
                    ),
                }
                for reason in sorted({t.exit_reason for t in base.trades})
            },
        }

    ranked = sorted(
        results.items(),
        key=lambda item: (
            item[1]["oos_stress"]["expectancy"],
            item[1]["oos_stress"]["profit_factor"],
        ),
        reverse=True,
    )
    payload = {
        "research_only": True,
        "timeframe_ms": 5_000,
        "symbol": symbol,
        "bars": len(rows),
        "oos_fraction": 0.30,
        "promotion_allowed": False,
        "reason": (
            "One-day high-frequency run is a feasibility study only. "
            "No profile may unlock Demo from this result."
        ),
        "ranking": [name for name, _ in ranked],
        "profiles": results,
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
