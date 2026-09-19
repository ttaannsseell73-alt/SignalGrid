from __future__ import annotations

import argparse
import json
from dataclasses import asdict, replace
from pathlib import Path

from signalgrid.backtest.data import load_binance_klines_csv
from signalgrid.backtest.engine import BacktestConfig, run_backtest
from signalgrid.models import Direction, Signal
from signalgrid.signals.scalping import ScalpingConfig, ScalpingSignalEngine


SETUP_CANDIDATES = (
    "BREAKOUT_RETEST",
    "LIQUIDITY_SWEEP_REJECTION",
    "BREAKOUT_ACCEPTANCE",
    "COMPRESSION_BREAKOUT",
)
DIRECTION_CANDIDATES = ("BOTH", "LONG", "SHORT")


class SetupDirectionFilter:
    def __init__(self, inner: ScalpingSignalEngine, setup: str, direction: str):
        self.inner = inner
        self.setup = setup
        self.direction = direction

    def evaluate(self, state):
        signal = self.inner.evaluate(state)
        if signal.direction is Direction.PASS:
            return signal
        if signal.setup != self.setup:
            return Signal.pass_signal(state.symbol, "RESEARCH_SETUP_FILTER")
        if self.direction == "LONG" and signal.direction is not Direction.LONG:
            return Signal.pass_signal(state.symbol, "RESEARCH_DIRECTION_FILTER")
        if self.direction == "SHORT" and signal.direction is not Direction.SHORT:
            return Signal.pass_signal(state.symbol, "RESEARCH_DIRECTION_FILTER")
        return signal


def _parse_symbols(text: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(x.strip().upper() for x in text.split(",") if x.strip()))


def _bt(stress: bool) -> BacktestConfig:
    return BacktestConfig(
        starting_equity=10_000.0,
        notional_usdt=100.0,
        taker_fee_bps=7.0 if stress else 5.0,
        assumed_spread_bps=3.0 if stress else 1.5,
        slippage_bps=3.0 if stress else 1.0,
        max_holding_bars=6,
        require_book=False,
        take_profit_enabled=True,
        min_take_profit_bps=30.0,
        tp_reward_risk_multiple=2.0,
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="SignalGrid setup/direction scalp research matrix"
    )
    p.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT")
    p.add_argument("--data-dir", default="data/scalping")
    p.add_argument("--output", default="validation/scalping_setup_direction_matrix.json")
    p.add_argument("--min-oos-trades", type=int, default=20)
    args = p.parse_args(argv)

    symbols = _parse_symbols(args.symbols)
    if not symbols:
        p.error("at least one symbol is required")

    datasets = {}
    data_dir = Path(args.data_dir)
    for symbol in symbols:
        path = data_dir / f"{symbol}-1m.csv"
        if not path.exists():
            raise SystemExit(f"missing historical data: {path}")
        rows = load_binance_klines_csv(path, symbol)
        split = max(1, min(len(rows) - 1, int(len(rows) * 0.70)))
        datasets[symbol] = (rows, rows[split].open_time_ms)

    signal_cfg = replace(
        ScalpingConfig(require_book_microstructure=False),
        min_stop_bps=24.0,
        min_score=0.70,
    )

    candidates = []
    for setup in SETUP_CANDIDATES:
        for direction in DIRECTION_CANDIDATES:
            per_symbol = {}
            cross_symbol_pass = True
            for symbol in symbols:
                rows, oos_start = datasets[symbol]
                base_engine = SetupDirectionFilter(
                    ScalpingSignalEngine(signal_cfg), setup, direction
                )
                stress_engine = SetupDirectionFilter(
                    ScalpingSignalEngine(signal_cfg), setup, direction
                )
                full_base = run_backtest(
                    rows,
                    backtest_config=_bt(False),
                    engine=base_engine,
                )
                oos_stress = run_backtest(
                    rows,
                    backtest_config=_bt(True),
                    engine=stress_engine,
                    trade_start_ms=oos_start,
                )
                fm = full_base.metrics
                om = oos_stress.metrics
                symbol_pass = (
                    fm.trades >= args.min_oos_trades
                    and fm.expectancy > 0
                    and fm.profit_factor > 1.0
                    and om.trades >= args.min_oos_trades
                    and om.expectancy > 0
                    and om.profit_factor > 1.0
                )
                cross_symbol_pass = cross_symbol_pass and symbol_pass
                per_symbol[symbol] = {
                    "full_base": asdict(fm),
                    "oos_stress": asdict(om),
                    "pass": symbol_pass,
                }

            candidates.append(
                {
                    "setup": setup,
                    "direction": direction,
                    "cross_symbol_pass": cross_symbol_pass,
                    "symbols": per_symbol,
                    "worst_oos_stress_expectancy": min(
                        x["oos_stress"]["expectancy"] for x in per_symbol.values()
                    ),
                    "mean_oos_stress_expectancy": sum(
                        x["oos_stress"]["expectancy"] for x in per_symbol.values()
                    ) / len(per_symbol),
                    "min_oos_trades": min(
                        x["oos_stress"]["trades"] for x in per_symbol.values()
                    ),
                }
            )

    ranked = sorted(
        candidates,
        key=lambda x: (
            x["cross_symbol_pass"],
            x["worst_oos_stress_expectancy"],
            x["mean_oos_stress_expectancy"],
            x["min_oos_trades"],
        ),
        reverse=True,
    )
    payload = {
        "research_only": True,
        "fixed_profile": {
            "min_stop_bps": 24.0,
            "min_score": 0.70,
            "reward_risk": 2.0,
            "max_holding_bars": 6,
        },
        "setups": list(SETUP_CANDIDATES),
        "directions": list(DIRECTION_CANDIDATES),
        "candidate_count": len(ranked),
        "passing_candidates": sum(1 for x in ranked if x["cross_symbol_pass"]),
        "promotion_rule": (
            "No automatic promotion. A candidate must have positive full-base "
            "and OOS-stress expectancy with PF>1 and adequate trades on BTC, ETH and SOL."
        ),
        "ranking": ranked,
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
