from __future__ import annotations
from dataclasses import dataclass
from signalgrid.market.state import Bar

@dataclass(slots=True)
class StructureSignal:
    direction: int
    setup: str
    invalidation: float | None

def detect_structure(bars: list[Bar], lookback: int = 20) -> StructureSignal:
    if len(bars) < lookback + 1:
        return StructureSignal(0, "WARMUP", None)
    cur = bars[-1]
    prior = bars[-(lookback + 1):-1]
    hi = max(b.high for b in prior)
    lo = min(b.low for b in prior)
    if cur.close > hi:
        return StructureSignal(+1, "BREAKOUT_ACCEPTANCE", hi)
    if cur.close < lo:
        return StructureSignal(-1, "BREAKOUT_ACCEPTANCE", lo)
    if cur.high > hi and cur.close < hi:
        return StructureSignal(-1, "FAILED_BREAKOUT_SWEEP", cur.high)
    if cur.low < lo and cur.close > lo:
        return StructureSignal(+1, "FAILED_BREAKOUT_SWEEP", cur.low)
    return StructureSignal(0, "RANGE", None)
