from __future__ import annotations

import argparse
import json
from dataclasses import asdict, replace
from pathlib import Path

from signalgrid.backtest.data import load_binance_klines_csv
from signalgrid.backtest.engine import BacktestConfig, run_backtest
from signalgrid.signals.scalping import ScalpingConfig, ScalpingSignalEngine


RR_CANDIDATES = (1.5, 2.0, 2.5)
HOLD_CANDIDATES = (6, 12)


def _parse_symbols(text: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(x.strip().upper() for x in text.split(",") if x.strip()))


def _bt_config(max_holding_bars: int, rr: float, stress: bool) -> BacktestConfig:
    return BacktestConfig(
        starting_equity=10_000.0,
        notional_usdt=100.0,
        taker_fee_bps=7.0 if stress else 5.0,
        assumed_spread_bps=3.0 if stress else 1.5,
        slippage_bps=3.0 if stress else 1.0,
        max_holding_bars=max_holding_bars,
        require_book=False,
        take_profit_enabled=True,
        min_take_profit_bps=30.0,
        tp_reward_risk_multiple=rr,
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="SignalGrid reward-risk scalp research matrix"
    )
    p.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT")
    p.add_argument("--data-dir", default="data/scalping")
    p.add_argument("--output", default="validation/scalping_rr_matrix.json")
    p.add_argument("--min-oos-trades", type=int, default=30)
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

    # This research starts from the best quality-filter region from the prior
    # pre-declared matrix. It does not promote itself into production.
    signal_cfg = replace(
        ScalpingConfig(require_book_microstructure=False),
        min_stop_bps=24.0,
        min_score=0.70,
    )

    candidates: list[dict] = []
    for rr in RR_CANDIDATES:
        for hold in HOLD_CANDIDATES:
            per_symbol: dict[str, dict] = {}
            cross_symbol_pass = True
            for symbol in symbols:
                rows, oos_start_ms = datasets[symbol]
                full_base = run_backtest(
                    rows,
                    backtest_config=_bt_config(hold, rr, False),
                    engine=ScalpingSignalEngine(signal_cfg),
                )
                oos_stress = run_backtest(
                    rows,
                    backtest_config=_bt_config(hold, rr, True),
                    engine=ScalpingSignalEngine(signal_cfg),
                    trade_start_ms=oos_start_ms,
                )
                fm = full_base.metrics
                om = oos_stress.metrics
                symbol_pass = (
                    fm.expectancy > 0
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
                    "reward_risk": rr,
                    "max_holding_bars": hold,
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
        ),
        reverse=True,
    )
    payload = {
        "research_only": True,
        "fixed_signal_filter": {"min_stop_bps": 24.0, "min_score": 0.70},
        "reward_risk_candidates": list(RR_CANDIDATES),
        "holding_candidates": list(HOLD_CANDIDATES),
        "candidate_count": len(ranked),
        "passing_candidates": sum(1 for x in ranked if x["cross_symbol_pass"]),
        "promotion_rule": (
            "No automatic promotion. Candidate must be positive in full-base and "
            "OOS-stress expectancy with PF>1 on BTC, ETH and SOL."
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
