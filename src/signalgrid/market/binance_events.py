from __future__ import annotations
from dataclasses import dataclass
from typing import Any
from signalgrid.market.state import Bar, SymbolState

@dataclass(slots=True)
class EventResult:
    symbol: str
    kind: str
    changed: bool

class BinanceMarketEventRouter:
    """Pure event parser/state updater for Binance USDⓈ-M public stream payloads.

    Transport is intentionally separate. This class is deterministic and testable.
    """

    def __init__(self):
        self.states: dict[str, SymbolState] = {}
        self._kline_open_time: dict[str, int] = {}

    def state(self, symbol: str) -> SymbolState:
        symbol = symbol.upper()
        if symbol not in self.states:
            self.states[symbol] = SymbolState(symbol)
        return self.states[symbol]

    def on_message(self, message: dict[str, Any]) -> EventResult | None:
        data = message.get("data", message)
        event = data.get("e")
        symbol = data.get("s")
        if not symbol:
            return None
        state = self.state(symbol)

        if event == "aggTrade":
            quote = float(data["p"]) * float(data["q"])
            buyer_is_maker = bool(data.get("m", False))
            if buyer_is_maker:
                state.taker_sell_quote += quote
            else:
                state.taker_buy_quote += quote
            return EventResult(state.symbol, "aggTrade", True)

        if event == "bookTicker":
            state.best_bid = float(data["b"])
            state.best_ask = float(data["a"])
            state.bid_depth = float(data.get("B", 0.0))
            state.ask_depth = float(data.get("A", 0.0))
            return EventResult(state.symbol, "bookTicker", True)

        if event == "kline":
            k = data["k"]
            bar = Bar(
                open=float(k["o"]),
                high=float(k["h"]),
                low=float(k["l"]),
                close=float(k["c"]),
                volume=float(k.get("v", 0.0)),
            )
            open_time = int(k["t"])
            if self._kline_open_time.get(state.symbol) == open_time and state.bars:
                state.bars[-1] = bar
            else:
                state.add_bar(bar)
                self._kline_open_time[state.symbol] = open_time
            return EventResult(state.symbol, "kline", True)

        return EventResult(state.symbol, str(event or "unknown"), False)
