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
    """Pure event parser/state updater for Binance USDⓈ-M public stream payloads."""

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
        event_time = int(data.get("E") or data.get("T") or 0)
        if event_time:
            state.last_event_time_ms = max(state.last_event_time_ms or 0, event_time)

        if event == "aggTrade":
            quote = float(data["p"]) * float(data["q"])
            trade_time = int(data.get("T") or data.get("E") or 0)
            state.add_taker_trade(trade_time, quote, bool(data.get("m", False)))
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
