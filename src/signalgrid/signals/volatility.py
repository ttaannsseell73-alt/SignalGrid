from __future__ import annotations
from signalgrid.market.state import Bar


def _true_ranges(bars: list[Bar]) -> list[float]:
    if len(bars) < 2:
        return []
    out: list[float] = []
    prev_close = bars[0].close
    for bar in bars[1:]:
        out.append(max(bar.high - bar.low, abs(bar.high - prev_close), abs(bar.low - prev_close)))
        prev_close = bar.close
    return out


def natr(bars: list[Bar], period: int = 14) -> float | None:
    trs = _true_ranges(bars)
    if len(trs) < period or bars[-1].close <= 0:
        return None
    atr = sum(trs[-period:]) / period
    return atr / bars[-1].close


def volatility_expansion(bars: list[Bar], short: int = 10, long: int = 50) -> float | None:
    trs = _true_ranges(bars)
    if len(trs) < long:
        return None
    short_v = sum(trs[-short:]) / short
    long_v = sum(trs[-long:]) / long
    if long_v <= 0:
        return None
    return short_v / long_v
