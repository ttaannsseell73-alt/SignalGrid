from __future__ import annotations

def taker_imbalance(buy_quote: float, sell_quote: float) -> float:
    total = buy_quote + sell_quote
    if total <= 0:
        return 0.0
    return (buy_quote - sell_quote) / total

def book_imbalance(bid_depth: float, ask_depth: float) -> float:
    total = bid_depth + ask_depth
    if total <= 0:
        return 0.0
    return (bid_depth - ask_depth) / total

def spread_bps(best_bid: float | None, best_ask: float | None) -> float | None:
    if not best_bid or not best_ask or best_bid <= 0 or best_ask < best_bid:
        return None
    mid = (best_bid + best_ask) / 2
    return (best_ask - best_bid) / mid * 10_000
