from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from signalgrid.backtest.data import HistoricalDataError, normalize_timestamp_ms


@dataclass(frozen=True, slots=True)
class BookDepthSnapshot:
    symbol: str
    timestamp_ms: int
    bid_depth_1: float
    ask_depth_1: float
    bid_depth_5: float
    ask_depth_5: float
    bid_notional_1: float
    ask_notional_1: float
    bid_notional_5: float
    ask_notional_5: float

    @staticmethod
    def _imbalance(bid: float, ask: float) -> float:
        total = bid + ask
        return (bid - ask) / total if total > 0 else 0.0

    @property
    def depth_imbalance_1(self) -> float:
        return self._imbalance(self.bid_depth_1, self.ask_depth_1)

    @property
    def depth_imbalance_5(self) -> float:
        return self._imbalance(self.bid_depth_5, self.ask_depth_5)

    @property
    def notional_imbalance_1(self) -> float:
        return self._imbalance(self.bid_notional_1, self.ask_notional_1)

    @property
    def notional_imbalance_5(self) -> float:
        return self._imbalance(self.bid_notional_5, self.ask_notional_5)


def _timestamp_ms(value: str) -> int:
    text = value.strip()
    try:
        return normalize_timestamp_ms(text)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            dt = datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
            return int(dt.timestamp() * 1000)
        except ValueError:
            continue
    raise HistoricalDataError(f"invalid bookDepth timestamp: {value!r}")


def load_bookdepth_csv(path: str | Path, symbol: str) -> list[BookDepthSnapshot]:
    groups: dict[int, dict[int, tuple[float, float]]] = {}
    with Path(path).open("r", newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh)
        for raw in reader:
            if not raw or len(raw) < 4:
                continue
            if raw[0].strip().lower() == "timestamp":
                continue
            try:
                ts = _timestamp_ms(raw[0])
                percentage = int(float(raw[1]))
                depth = float(raw[2])
                notional = float(raw[3])
            except (ValueError, HistoricalDataError) as exc:
                raise HistoricalDataError(f"invalid bookDepth row: {raw[:4]}") from exc
            if percentage not in {-5, -4, -3, -2, -1, 1, 2, 3, 4, 5}:
                raise HistoricalDataError(f"unexpected bookDepth percentage: {percentage}")
            if depth < 0 or notional < 0:
                raise HistoricalDataError("bookDepth depth/notional must be non-negative")
            groups.setdefault(ts, {})[percentage] = (depth, notional)

    out: list[BookDepthSnapshot] = []
    required = {-5, -1, 1, 5}
    for ts in sorted(groups):
        levels = groups[ts]
        if not required.issubset(levels):
            continue
        out.append(
            BookDepthSnapshot(
                symbol=symbol.upper(),
                timestamp_ms=ts,
                bid_depth_1=levels[-1][0],
                ask_depth_1=levels[1][0],
                bid_depth_5=levels[-5][0],
                ask_depth_5=levels[5][0],
                bid_notional_1=levels[-1][1],
                ask_notional_1=levels[1][1],
                bid_notional_5=levels[-5][1],
                ask_notional_5=levels[5][1],
            )
        )
    for left, right in zip(out, out[1:]):
        if right.timestamp_ms <= left.timestamp_ms:
            raise HistoricalDataError("bookDepth timestamps must be strictly increasing")
    return out


def depth_quality_report(snapshots: list[BookDepthSnapshot]) -> dict:
    if not snapshots:
        return {
            "snapshots": 0,
            "median_interval_ms": 0,
            "frozen_inner_ask_fraction": 0.0,
            "frozen_inner_bid_fraction": 0.0,
        }
    intervals = [
        right.timestamp_ms - left.timestamp_ms
        for left, right in zip(snapshots, snapshots[1:])
    ]
    ordered = sorted(intervals)
    median_interval = ordered[len(ordered) // 2] if ordered else 0

    ask_same = sum(
        1
        for left, right in zip(snapshots, snapshots[1:])
        if right.ask_depth_1 == left.ask_depth_1
        and right.ask_notional_1 == left.ask_notional_1
    )
    bid_same = sum(
        1
        for left, right in zip(snapshots, snapshots[1:])
        if right.bid_depth_1 == left.bid_depth_1
        and right.bid_notional_1 == left.bid_notional_1
    )
    denom = max(1, len(snapshots) - 1)
    return {
        "snapshots": len(snapshots),
        "median_interval_ms": median_interval,
        "frozen_inner_ask_fraction": ask_same / denom,
        "frozen_inner_bid_fraction": bid_same / denom,
        "mean_abs_depth_imbalance_1": (
            sum(abs(x.depth_imbalance_1) for x in snapshots) / len(snapshots)
        ),
        "mean_abs_depth_imbalance_5": (
            sum(abs(x.depth_imbalance_5) for x in snapshots) / len(snapshots)
        ),
    }
