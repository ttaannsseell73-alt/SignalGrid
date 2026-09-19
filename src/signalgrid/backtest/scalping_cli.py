from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from signalgrid.backtest.data import (
    attach_book_snapshots,
    load_binance_klines_csv,
    load_book_ticker_csv,
    load_funding_rate_csv,
)
from signalgrid.backtest.scalping_validation import (
    ScalpingValidationGate,
    run_scalping_validation,
)
from signalgrid.signals.scalping import SCALPING_PROFILE_VERSION


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="SignalGrid Scalping V1 no-lookahead cost-stressed validation"
    )
    p.add_argument("--symbol", required=True)
    p.add_argument("--klines", required=True, help="Binance USD-M 1m kline CSV")
    p.add_argument("--bookticker", required=True, help="Binance USD-M historical bookTicker CSV")
    p.add_argument("--funding", help="Optional Binance fundingRate CSV")
    p.add_argument("--book-max-age-ms", type=int, default=2_000)
    p.add_argument("--notional", type=float, default=100.0)
    p.add_argument("--min-trades", type=int, default=50)
    p.add_argument("--min-profit-factor", type=float, default=1.05)
    p.add_argument("--max-drawdown-pct", type=float, default=0.15)
    p.add_argument("--max-cost-share", type=float, default=0.70)
    p.add_argument("--gate-output", help="Write a Demo unlock marker only when validation passes")
    args = p.parse_args(argv)

    bars = load_binance_klines_csv(args.klines, args.symbol)
    books = load_book_ticker_csv(args.bookticker, args.symbol)
    bars = attach_book_snapshots(bars, books, max_age_ms=args.book_max_age_ms)
    missing = sum(1 for b in bars if not b.has_book)
    if missing:
        raise SystemExit(
            "book coverage incomplete: "
            f"{missing}/{len(bars)} bars lack an at-or-before snapshot "
            f"within {args.book_max_age_ms} ms"
        )

    funding = load_funding_rate_csv(args.funding, args.symbol) if args.funding else []
    gate = ScalpingValidationGate(
        min_trades=args.min_trades,
        min_base_profit_factor=args.min_profit_factor,
        max_base_drawdown_pct=args.max_drawdown_pct,
        max_base_cost_share=args.max_cost_share,
    )
    report = run_scalping_validation(
        bars,
        gate=gate,
        funding_points=funding,
        notional_usdt=args.notional,
    )

    payload = {
        "strategy": "SCALPING_V1",
        "symbol": args.symbol.upper(),
        "bars": len(bars),
        "gate_passed": report.passed,
        "gate_reasons": list(report.reasons),
        "history_span_days": report.history_span_days,
        "base": asdict(report.base.metrics),
        "stress": asdict(report.stress.metrics),
        "by_setup": [asdict(x) for x in report.by_setup],
        "by_regime": [asdict(x) for x in report.by_regime],
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    if report.passed and args.gate_output:
        gate_path = Path(args.gate_output)
        gate_path.parent.mkdir(parents=True, exist_ok=True)
        gate_path.write_text(
            json.dumps(
                {
                    "gate_passed": True,
                    "profile_version": SCALPING_PROFILE_VERSION,
                    "symbol": args.symbol.upper(),
                    "bars": len(bars),
                    "history_span_days": report.history_span_days,
                    "base": asdict(report.base.metrics),
                    "stress": asdict(report.stress.metrics),
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
    return 0 if report.passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
