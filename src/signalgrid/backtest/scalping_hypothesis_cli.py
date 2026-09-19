from __future__ import annotations

import argparse
import json
from dataclasses import asdict, replace
from pathlib import Path

from signalgrid.backtest.data import load_binance_klines_csv
from signalgrid.backtest.engine import BacktestConfig, run_backtest
from signalgrid.signals.scalping import ScalpingConfig, ScalpingSignalEngine


STOP_CANDIDATES = (12.0, 18.0, 24.0)
SCORE_CANDIDATES = (0.62, 0.70, 0.78)


def _parse_symbols(text: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(x.strip().upper() for x in text.split(",") if x.strip()))


def _configs() -> tuple[BacktestConfig, BacktestConfig]:
    base = BacktestConfig(
        starting_equity=10_000.0,
        notional_usdt=100.0,
        taker_fee_bps=5.0,
        assumed_spread_bps=1.5,
        slippage_bps=1.0,
        max_holding_bars=6,
        require_book=False,
        take_profit_enabled=True,
        tp_spacing_natr_multiplier=0.20,
        tp_min_spacing_bps=4.0,
        tp_max_spacing_bps=25.0,
        tp_steps=1.2,
        min_take_profit_bps=30.0,
    )
    stress = replace(
        base,
        taker_fee_bps=7.0,
        assumed_spread_bps=3.0,
        slippage_bps=3.0,
    )
    return base, stress


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Pre-declared SignalGrid scalp quality-filter research matrix"
    )
    p.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT")
    p.add_argument("--data-dir", default="data/scalping")
    p.add_argument("--output", default="validation/scalping_hypothesis_matrix.json")
    p.add_argument("--min-oos-trades", type=int, default=30)
    args = p.parse_args(argv)

    symbols = _parse_symbols(args.symbols)
    if not symbols:
        p.error("at least one symbol is required")

    data_dir = Path(args.data_dir)
    datasets = {}
    for symbol in symbols:
        path = data_dir / f"{symbol}-1m.csv"
        if not path.exists():
            raise SystemExit(f"missing historical data: {path}")
        rows = load_binance_klines_csv(path, symbol)
        split = max(1, min(len(rows) - 1, int(len(rows) * 0.70)))
        datasets[symbol] = (rows, rows[split].open_time_ms)

    base_bt, stress_bt = _configs()
    baseline = ScalpingConfig(require_book_microstructure=False)
    candidates: list[dict] = []

    for min_stop_bps in STOP_CANDIDATES:
        for min_score in SCORE_CANDIDATES:
            signal_cfg = replace(
                baseline,
                min_stop_bps=min_stop_bps,
                min_score=min_score,
            )
            per_symbol: dict[str, dict] = {}
            cross_symbol_pass = True
            for symbol in symbols:
                rows, oos_start_ms = datasets[symbol]
                full_base = run_backtest(
                    rows,
                    backtest_config=base_bt,
                    engine=ScalpingSignalEngine(signal_cfg),
                )
                oos_stress = run_backtest(
                    rows,
                    backtest_config=stress_bt,
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
                    "min_stop_bps": min_stop_bps,
                    "min_score": min_score,
                    "cross_symbol_pass": cross_symbol_pass,
                    "symbols": per_symbol,
                    "worst_oos_stress_expectancy": min(
                        item["oos_stress"]["expectancy"] for item in per_symbol.values()
                    ),
                    "mean_oos_stress_expectancy": sum(
                        item["oos_stress"]["expectancy"] for item in per_symbol.values()
                    ) / len(per_symbol),
                    "min_oos_trades": min(
                        item["oos_stress"]["trades"] for item in per_symbol.values()
                    ),
                }
            )

    ranked = sorted(
        candidates,
        key=lambda item: (
            item["cross_symbol_pass"],
            item["worst_oos_stress_expectancy"],
            item["mean_oos_stress_expectancy"],
        ),
        reverse=True,
    )
    payload = {
        "research_only": True,
        "promotion_rule": (
            "No candidate changes the canonical strategy automatically. "
            "Promotion requires positive full-base and OOS-stress expectancy/PF>1 "
            "on every requested symbol with adequate OOS trades."
        ),
        "symbols": list(symbols),
        "stop_candidates": list(STOP_CANDIDATES),
        "score_candidates": list(SCORE_CANDIDATES),
        "candidate_count": len(ranked),
        "passing_candidates": sum(1 for item in ranked if item["cross_symbol_pass"]),
        "ranking": ranked,
    }

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
