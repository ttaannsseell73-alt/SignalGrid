from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from signalgrid.backtest.data import load_binance_klines_csv, load_funding_rate_csv
from signalgrid.backtest.scalping_validation import ScalpingValidationGate, run_scalping_validation
from signalgrid.signals.scalping import SCALPING_PROFILE_VERSION


def parse_symbols(text: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(x.strip().upper() for x in text.split(",") if x.strip()))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Multi-symbol SignalGrid Scalping V1 historical validation")
    p.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT")
    p.add_argument("--data-dir", default="data/scalping")
    p.add_argument("--notional", type=float, default=100.0)
    p.add_argument("--gate-output", default="validation/scalping_gate.json")
    p.add_argument("--min-trades", type=int, default=50)
    p.add_argument("--min-profit-factor", type=float, default=1.05)
    p.add_argument("--max-drawdown-pct", type=float, default=0.15)
    p.add_argument("--max-cost-share", type=float, default=0.70)
    args = p.parse_args(argv)

    symbols = parse_symbols(args.symbols)
    if not symbols:
        p.error("at least one symbol is required")

    data_dir = Path(args.data_dir)
    gate = ScalpingValidationGate(
        min_trades=args.min_trades,
        min_base_profit_factor=args.min_profit_factor,
        max_base_drawdown_pct=args.max_drawdown_pct,
        max_base_cost_share=args.max_cost_share,
    )

    results: dict[str, dict] = {}
    validated: list[str] = []
    scopes: set[str] = set()

    for symbol in symbols:
        klines = data_dir / f"{symbol}-1m.csv"
        if not klines.exists():
            results[symbol] = {"passed": False, "reasons": [f"MISSING_KLINES:{klines}"]}
            continue

        funding_path = data_dir / f"{symbol}-fundingRate.csv"
        rows = load_binance_klines_csv(klines, symbol)
        funding = load_funding_rate_csv(funding_path, symbol) if funding_path.exists() else []
        report = run_scalping_validation(
            rows,
            gate=gate,
            funding_points=funding,
            notional_usdt=args.notional,
        )
        scopes.add(report.historical_microstructure_scope)
        results[symbol] = {
            "passed": report.passed,
            "bars": len(rows),
            "history_span_days": report.history_span_days,
            "reasons": list(report.reasons),
            "base": asdict(report.base.metrics),
            "stress": asdict(report.stress.metrics),
            "oos_base": asdict(report.oos_base.metrics),
            "oos_stress": asdict(report.oos_stress.metrics),
            "robustness": asdict(report.robustness),
            "by_setup": [asdict(x) for x in report.by_setup],
            "by_regime": [asdict(x) for x in report.by_regime],
        }
        if report.passed:
            validated.append(symbol)

    all_passed = len(validated) == len(symbols)
    payload = {
        "strategy": "SCALPING_V1",
        "profile_version": SCALPING_PROFILE_VERSION,
        "requested_symbols": list(symbols),
        "validated_symbols": validated,
        "all_passed": all_passed,
        "symbols": results,
    }
    print(json.dumps(payload, indent=2, sort_keys=True))

    gate_path = Path(args.gate_output)
    if all_passed:
        if scopes != {"PRICE_ACTION_TAKER_ONLY"}:
            raise RuntimeError("portfolio historical microstructure scope is inconsistent")
        gate_path.parent.mkdir(parents=True, exist_ok=True)
        gate_path.write_text(
            json.dumps(
                {
                    "gate_passed": True,
                    "profile_version": SCALPING_PROFILE_VERSION,
                    "validated_symbols": validated,
                    "historical_microstructure_scope": "PRICE_ACTION_TAKER_ONLY",
                    "portfolio_results": results,
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        return 0

    gate_path.unlink(missing_ok=True)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
