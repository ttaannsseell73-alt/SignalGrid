from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class StreamSpec:
    symbol: str
    interval: str = "1m"

    @property
    def names(self) -> tuple[str, str, str]:
        s = self.symbol.lower()
        return (f"{s}@aggTrade", f"{s}@bookTicker", f"{s}@kline_{self.interval}")

def combined_stream_names(symbols: list[str], interval: str = "1m") -> list[str]:
    out: list[str] = []
    for symbol in symbols:
        out.extend(StreamSpec(symbol, interval).names)
    return out
