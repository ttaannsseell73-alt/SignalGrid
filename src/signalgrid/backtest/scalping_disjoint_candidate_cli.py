from __future__ import annotations

import argparse
import json
from dataclasses import asdict, replace
from pathlib import Path

from signalgrid.backtest.data import load_binance_klines_csv
from signalgrid.backtest.engine import BacktestConfig, run_backtest
from signalgrid.backtest.scalping_setup_direction_cli import SetupDirectionFilter
from signalgrid.signals.scalping import ScalpingConfig, ScalpingSignalEngine


FROZEN_CANDIDATE = {
    "setup": "LIQUIDITY_SWEEP_REJECTION",
    "direction": "LONG",
    "min_stop_bps": 24.0,
    "min_score": 0.70,
    "reward_risk": 2.0,
    "max_holding_bars": 6,
}


def _parse_symbols(text: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(x.strip().upper() for x in text.split(",") if x.strip()))


def _bt(stress: bool) -> BacktestConfig:
    return BacktestConfig(
        starting_equity=10_000.0,
        notional_usdt=100.0,
        taker_fee_bps=7.0 if stress else 5.0,
        assumed_spread_bps=3.0 if stress else 1.5,
        slippage_bps=3.0 if stress else 1.0,
        max_holding_bars=FROZEN_CANDIDATE["max_holding_bars"],
        require_book=False,
        take_profit_enabled=True,
        min_take_profit_bps=30.0,
        tp_reward_risk_multiple=FROZEN_CANDIDATE["reward_risk"],
    )


def _engine() -> SetupDirectionFilter:
    cfg = replace(
        ScalpingConfig(require_book_microstructure=False),
        min_stop_bps=FROZEN_CANDIDATE["min_stop_bps"],
        min_score=FROZEN_CANDIDATE["min_score"],
    )
    return SetupDirectionFilter(
        ScalpingSignalEngine(cfg),
        FROZEN_CANDIDATE["setup"],
        FROZEN_CANDIDATE["direction"],
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Disjoint-period validation for frozen long-liquidity-sweep candidate"
    )
    p.add_argument("--symbols", default="BTCUSDT,ETHUSDT")
    p.add_argument("--data-dir", default="data/scalping-disjoint")
    p.add_argument("--output", default="validation/scalping_disjoint_sweep_long.json")
    p.add_argument("--min-trades", type=int, default=40)
    args = p.parse_args(argv)

    symbols = _parse_symbols(args.symbols)
    if not symbols:
        p.error("at least one symbol is required")

    results = {}
    all_pass = True
    for symbol in symbols:
        path = Path(args.data_dir) / f"{symbol}-1m.csv"
        if not path.exists():
            raise SystemExit(f"missing disjoint data: {path}")
        rows = load_binance_klines_csv(path, symbol)
        base = run_backtest(rows, backtest_config=_bt(False), engine=_engine())
        stress = run_backtest(rows, backtest_config=_bt(True), engine=_engine())
        bm = base.metrics
        sm = stress.metrics
        passed = (
            bm.trades >= args.min_trades
            and sm.trades >= args.min_trades
            and bm.expectancy > 0
            and bm.profit_factor > 1.0
            and sm.expectancy > 0
            and sm.profit_factor > 1.0
        )
        all_pass = all_pass and passed
        results[symbol] = {
            "passed": passed,
            "bars": len(rows),
            "base": asdict(bm),
            "stress": asdict(sm),
            "exit_reasons": {
                reason: {
                    "trades": sum(1 for t in stress.trades if t.exit_reason == reason),
                    "net_pnl": sum(t.net_pnl for t in stress.trades if t.exit_reason == reason),
                }
                for reason in sorted({t.exit_reason for t in stress.trades})
            },
        }

    payload = {
        "candidate_frozen_before_disjoint_test": True,
        "candidate": FROZEN_CANDIDATE,
        "symbols": list(symbols),
        "min_trades": args.min_trades,
        "all_passed": all_pass,
        "results": results,
        "interpretation": (
            "This is a disjoint historical test. A fail rejects this candidate. "
            "A pass only advances it to further validation; it does not unlock live capital."
        ),
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if all_pass else 2


if __name__ == "__main__":
    raise SystemExit(main())
