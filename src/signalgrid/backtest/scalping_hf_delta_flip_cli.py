from __future__ import annotations

import argparse
import json
from dataclasses import asdict, replace
from pathlib import Path

from signalgrid.backtest.data import load_binance_klines_csv
from signalgrid.backtest.engine import BacktestConfig, run_backtest
from signalgrid.models import Direction, Signal
from signalgrid.signals.flow import taker_imbalance
from signalgrid.signals.scalping import ScalpingConfig, ScalpingSignalEngine


DELTA_THRESHOLDS = (0.05, 0.10, 0.20)
DIRECTIONS = ("BOTH", "LONG", "SHORT")


class SweepDeltaFlipEngine:
    """Research-only sweep filter requiring aggressive-flow sign reversal."""

    def __init__(self, threshold: float, direction: str):
        if threshold <= 0:
            raise ValueError("threshold must be positive")
        self.threshold = threshold
        self.direction = direction
        self.inner = ScalpingSignalEngine(
            replace(
                ScalpingConfig(require_book_microstructure=False),
                structure_lookback=24,
                swing_window=12,
                compression_lookback=18,
                compression_baseline=48,
                retest_tolerance_bps=4.0,
                min_natr_bps=0.5,
                max_natr_bps=40.0,
                min_directional_flow=0.05,
                min_score=0.68,
                min_stop_bps=2.0,
                max_stop_bps=50.0,
            )
        )
        self.previous_flow: dict[str, float] = {}

    def evaluate(self, state):
        current = taker_imbalance(state.taker_buy_quote, state.taker_sell_quote)
        previous = self.previous_flow.get(state.symbol)
        signal = self.inner.evaluate(state)
        self.previous_flow[state.symbol] = current

        if signal.direction is Direction.PASS:
            return signal
        if signal.setup != "LIQUIDITY_SWEEP_REJECTION":
            return Signal.pass_signal(state.symbol, "HF_DELTA_SETUP_FILTER")
        if self.direction == "LONG" and signal.direction is not Direction.LONG:
            return Signal.pass_signal(state.symbol, "HF_DELTA_DIRECTION_FILTER")
        if self.direction == "SHORT" and signal.direction is not Direction.SHORT:
            return Signal.pass_signal(state.symbol, "HF_DELTA_DIRECTION_FILTER")
        if previous is None:
            return Signal.pass_signal(state.symbol, "HF_DELTA_WARMUP")

        if signal.direction is Direction.LONG:
            confirmed = previous <= -self.threshold and current >= self.threshold
        else:
            confirmed = previous >= self.threshold and current <= -self.threshold
        if not confirmed:
            return Signal.pass_signal(state.symbol, "HF_DELTA_FLIP_NOT_CONFIRMED")
        return signal


def _bt(stress: bool) -> BacktestConfig:
    return BacktestConfig(
        starting_equity=10_000.0,
        notional_usdt=100.0,
        taker_fee_bps=7.0 if stress else 5.0,
        assumed_spread_bps=3.0 if stress else 1.0,
        slippage_bps=3.0 if stress else 0.75,
        max_holding_bars=12,
        require_book=False,
        take_profit_enabled=True,
        min_take_profit_bps=20.0,
        tp_reward_risk_multiple=2.0,
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="5s liquidity-sweep delta-flip research")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--bars", required=True)
    p.add_argument("--output", default="validation/scalping_hf_delta_flip.json")
    args = p.parse_args(argv)

    symbol = args.symbol.upper()
    rows = load_binance_klines_csv(args.bars, symbol)
    split = max(1, min(len(rows) - 1, int(len(rows) * 0.70)))
    oos_start = rows[split].open_time_ms
    candidates = []

    for threshold in DELTA_THRESHOLDS:
        for direction in DIRECTIONS:
            base = run_backtest(
                rows,
                backtest_config=_bt(False),
                engine=SweepDeltaFlipEngine(threshold, direction),
            )
            oos = run_backtest(
                rows,
                backtest_config=_bt(True),
                engine=SweepDeltaFlipEngine(threshold, direction),
                trade_start_ms=oos_start,
            )
            candidates.append(
                {
                    "threshold": threshold,
                    "direction": direction,
                    "base": asdict(base.metrics),
                    "oos_stress": asdict(oos.metrics),
                }
            )

    ranked = sorted(
        candidates,
        key=lambda x: (
            x["oos_stress"]["expectancy"],
            x["oos_stress"]["profit_factor"],
            x["oos_stress"]["trades"],
        ),
        reverse=True,
    )
    payload = {
        "research_only": True,
        "symbol": symbol,
        "timeframe_ms": 5_000,
        "candidate_count": len(ranked),
        "promotion_allowed": False,
        "hypothesis": (
            "Liquidity sweep is actionable only when aggressive trade flow flips "
            "from opposing pressure on the prior 5s bar to confirming pressure now."
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
