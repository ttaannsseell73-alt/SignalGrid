from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from time import time

from signalgrid.backtest.data import load_binance_klines_csv
from signalgrid.backtest.engine import BacktestConfig, run_backtest
from signalgrid.models import Direction, GridMode, Signal
from signalgrid.signals.flow import taker_imbalance


FLOW_THRESHOLDS = (0.10, 0.20, 0.30)
LOOKBACKS = (12, 24)


class SweepAbsorptionEngine:
    """Research-only PA + order-flow absorption hypothesis.

    A long requires sell aggression into a fresh local low that fails to hold
    below the prior range. A short is the mirror image. Opposing taker flow is
    intentional: the hypothesis is passive absorption, not momentum chasing.
    """

    def __init__(self, *, lookback: int, opposing_flow: float):
        self.lookback = lookback
        self.opposing_flow = opposing_flow

    def evaluate(self, state):
        bars = list(state.bars)
        if len(bars) < self.lookback + 1:
            return Signal.pass_signal(state.symbol, "HF_ABSORPTION_WARMUP")

        cur = bars[-1]
        prior = bars[-(self.lookback + 1):-1]
        prior_low = min(b.low for b in prior)
        prior_high = max(b.high for b in prior)
        avg_range = sum(max(0.0, b.high - b.low) for b in prior) / len(prior)
        avg_volume = sum(max(0.0, b.volume) for b in prior) / len(prior)
        cur_range = max(0.0, cur.high - cur.low)
        if avg_range <= 0 or cur_range <= 0:
            return Signal.pass_signal(state.symbol, "HF_ABSORPTION_NO_RANGE")

        flow = taker_imbalance(state.taker_buy_quote, state.taker_sell_quote)
        close_location = (cur.close - cur.low) / cur_range
        range_expansion = cur_range / avg_range
        volume_expansion = cur.volume / max(avg_volume, 1e-12)

        direction = Direction.PASS
        invalidation = None

        long_rejection = (
            cur.low < prior_low
            and cur.close > prior_low
            and close_location >= 0.60
            and flow <= -self.opposing_flow
        )
        short_rejection = (
            cur.high > prior_high
            and cur.close < prior_high
            and close_location <= 0.40
            and flow >= self.opposing_flow
        )

        # The sweep must be meaningful, not a tiny one-tick probe on dead flow.
        if range_expansion < 1.10 or volume_expansion < 1.10:
            return Signal.pass_signal(state.symbol, "HF_ABSORPTION_NO_EXPANSION")

        if long_rejection:
            direction = Direction.LONG
            invalidation = cur.low
        elif short_rejection:
            direction = Direction.SHORT
            invalidation = cur.high
        else:
            return Signal.pass_signal(state.symbol, "HF_ABSORPTION_NO_SETUP")

        now = time()
        return Signal(
            symbol=state.symbol,
            direction=direction,
            strength=min(
                1.0,
                0.55
                + 0.20 * min(1.0, abs(flow))
                + 0.10 * min(1.0, range_expansion / 2.0)
                + 0.15 * min(1.0, volume_expansion / 2.0),
            ),
            regime="HF_ABSORPTION_REVERSAL",
            setup="HF_SWEEP_ABSORPTION",
            invalidation=invalidation,
            liquidity_ok=True,
            expires_at=now + 3.0,
            created_at=now,
            grid_mode=(
                GridMode.LONG_GRID
                if direction is Direction.LONG
                else GridMode.SHORT_GRID
            ),
        )


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
        min_take_profit_bps=15.0,
        tp_reward_risk_multiple=2.0,
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="5s sweep-absorption research")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--bars", required=True)
    p.add_argument("--output", default="validation/scalping_hf_absorption.json")
    args = p.parse_args(argv)

    symbol = args.symbol.upper()
    rows = load_binance_klines_csv(args.bars, symbol)
    split = max(1, min(len(rows) - 1, int(len(rows) * 0.70)))
    oos_start = rows[split].open_time_ms
    candidates = []

    for lookback in LOOKBACKS:
        for threshold in FLOW_THRESHOLDS:
            base = run_backtest(
                rows,
                backtest_config=_bt(False),
                engine=SweepAbsorptionEngine(
                    lookback=lookback,
                    opposing_flow=threshold,
                ),
            )
            oos = run_backtest(
                rows,
                backtest_config=_bt(True),
                engine=SweepAbsorptionEngine(
                    lookback=lookback,
                    opposing_flow=threshold,
                ),
                trade_start_ms=oos_start,
            )
            candidates.append(
                {
                    "lookback": lookback,
                    "opposing_flow": threshold,
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
            "Opposing aggressive flow at a rejected local-extreme sweep indicates "
            "passive absorption and a short-horizon reversal opportunity."
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
