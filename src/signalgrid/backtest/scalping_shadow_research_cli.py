from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from pathlib import Path


HORIZON_SECONDS = (5, 15, 30)
FLOW_BINS = (
    (-1.01, -0.35, "FLOW_STRONG_SELL"),
    (-0.35, -0.10, "FLOW_SELL"),
    (-0.10, 0.10, "FLOW_BALANCED"),
    (0.10, 0.35, "FLOW_BUY"),
    (0.35, 1.01, "FLOW_STRONG_BUY"),
)
DEPTH_BINS = (
    (-1.01, -0.20, "DEPTH_ASK_HEAVY"),
    (-0.20, 0.20, "DEPTH_BALANCED"),
    (0.20, 1.01, "DEPTH_BID_HEAVY"),
)
MICRO_BINS = (
    (-10_000.0, -0.05, "MICRO_ASK"),
    (-0.05, 0.05, "MICRO_NEUTRAL"),
    (0.05, 10_000.0, "MICRO_BID"),
)


def _bucket(value: float | None, bins) -> str:
    if value is None:
        return "MISSING"
    for lo, hi, name in bins:
        if lo <= value < hi:
            return name
    return "OUTSIDE"


def _load_rows(db_path: str | Path, symbol: str) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        raw = conn.execute(
            """
            SELECT bucket_start_ms, best_bid, best_ask, spread_bps, microprice_bps,
                   flow_imbalance, trade_count, trade_range_bps,
                   depth_imbalance_5, depth_imbalance_20
            FROM microstructure_1s
            WHERE symbol = ?
            ORDER BY bucket_start_ms
            """,
            (symbol.upper(),),
        ).fetchall()
    finally:
        conn.close()

    rows = []
    for row in raw:
        bid = row["best_bid"]
        ask = row["best_ask"]
        if bid is None or ask is None or bid <= 0 or ask < bid:
            continue
        rows.append(
            {
                "t": int(row["bucket_start_ms"]),
                "mid": (float(bid) + float(ask)) / 2.0,
                "spread_bps": row["spread_bps"],
                "microprice_bps": row["microprice_bps"],
                "flow_imbalance": float(row["flow_imbalance"] or 0.0),
                "trade_count": int(row["trade_count"] or 0),
                "trade_range_bps": row["trade_range_bps"],
                "depth_imbalance_5": float(row["depth_imbalance_5"] or 0.0),
                "depth_imbalance_20": float(row["depth_imbalance_20"] or 0.0),
            }
        )
    return rows


def _cell(row: dict) -> tuple[str, str, str]:
    return (
        _bucket(row["flow_imbalance"], FLOW_BINS),
        _bucket(row["depth_imbalance_20"], DEPTH_BINS),
        _bucket(row["microprice_bps"], MICRO_BINS),
    )


def _stats(values: list[float]) -> dict:
    n = len(values)
    if n == 0:
        return {
            "samples": 0,
            "preferred_direction": "NONE",
            "gross_mean_bps": 0.0,
            "hit_rate": 0.0,
        }
    raw = sum(values) / n
    direction = "LONG" if raw >= 0 else "SHORT"
    signed = values if direction == "LONG" else [-x for x in values]
    mean_bps = sum(signed) / n
    return {
        "samples": n,
        "preferred_direction": direction,
        "gross_mean_bps": mean_bps,
        "hit_rate": sum(1 for x in signed if x > 0) / n,
        "net_after_2bps": mean_bps - 2.0,
        "net_after_4bps": mean_bps - 4.0,
        "net_after_8bps": mean_bps - 8.0,
    }


def analyze(rows: list[dict], min_samples: int) -> dict:
    by_time = {row["t"]: row for row in rows}
    results = {}
    for horizon in HORIZON_SECONDS:
        cells: dict[tuple[str, str, str], list[float]] = defaultdict(list)
        step = horizon * 1000
        for row in rows:
            future = by_time.get(row["t"] + step)
            if future is None:
                continue
            ret_bps = (future["mid"] - row["mid"]) / row["mid"] * 10_000.0
            cells[_cell(row)].append(ret_bps)

        ranked = []
        for cell, values in cells.items():
            if len(values) < min_samples:
                continue
            stat = _stats(values)
            ranked.append(
                {
                    "flow": cell[0],
                    "depth20": cell[1],
                    "microprice": cell[2],
                    **stat,
                }
            )
        ranked.sort(
            key=lambda x: (
                x["net_after_2bps"],
                x["samples"],
            ),
            reverse=True,
        )
        results[str(horizon)] = ranked

    return {
        "rows": len(rows),
        "horizons_seconds": list(HORIZON_SECONDS),
        "min_samples": min_samples,
        "research_only": True,
        "warning": (
            "Exploratory live-shadow study. Results cannot unlock Demo. "
            "Any candidate must be frozen and validated on later unseen shadow data."
        ),
        "horizons": results,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Analyze live 1s scalping shadow microstructure")
    p.add_argument("--db", required=True)
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--min-samples", type=int, default=10)
    p.add_argument("--output", default="validation/scalping_shadow_research.json")
    args = p.parse_args(argv)

    rows = _load_rows(args.db, args.symbol)
    if len(rows) < 60:
        raise SystemExit(f"shadow research requires at least 60 usable 1s rows, got {len(rows)}")

    payload = {
        "symbol": args.symbol.upper(),
        **analyze(rows, args.min_samples),
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
