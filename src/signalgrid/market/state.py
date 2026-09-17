from __future__ import annotations
from collections import deque
from dataclasses import dataclass, field

@dataclass(slots=True)
class Bar:
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

@dataclass(slots=True)
class SymbolState:
    symbol: str
    bars: deque[Bar] = field(default_factory=lambda: deque(maxlen=256))
    taker_buy_quote: float = 0.0
    taker_sell_quote: float = 0.0
    best_bid: float | None = None
    best_ask: float | None = None
    bid_depth: float = 0.0
    ask_depth: float = 0.0

    def add_bar(self, bar: Bar) -> None:
        self.bars.append(bar)

    def reset_flow_window(self) -> None:
        self.taker_buy_quote = 0.0
        self.taker_sell_quote = 0.0
