from __future__ import annotations

import asyncio
from dataclasses import dataclass
from time import time
from typing import Any, Iterable

from signalgrid.market.binance_events import BinanceMarketEventRouter
from signalgrid.market.state import Bar


class WarmupError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class WarmupConfig:
    interval: str = "1m"
    bars: int = 60

    def __post_init__(self) -> None:
        if self.bars < 50:
            raise ValueError("warmup requires at least 50 closed bars")


def _unwrap(value: Any) -> Any:
    data = getattr(value, "data", None)
    if callable(data):
        return _unwrap(data())
    root = getattr(value, "root", None)
    if root is not None:
        return root
    return value


def _row_values(row: Any) -> list[Any]:
    if isinstance(row, (list, tuple)):
        return list(row)
    root = getattr(row, "root", None)
    if isinstance(root, (list, tuple)):
        return list(root)
    to_dict = getattr(row, "to_dict", None)
    if callable(to_dict):
        raw = to_dict()
        if isinstance(raw, (list, tuple)):
            return list(raw)
        if isinstance(raw, dict):
            keys = ("openTime", "open", "high", "low", "close", "volume", "closeTime")
            if all(k in raw for k in keys):
                return [raw[k] for k in keys]
    if isinstance(row, dict):
        keys = ("openTime", "open", "high", "low", "close", "volume", "closeTime")
        if all(k in row for k in keys):
            return [row[k] for k in keys]
    raise WarmupError(f"unsupported kline row shape: {type(row)!r}")


class BinanceKlineWarmup:
    def __init__(self, rest_api: Any, router: BinanceMarketEventRouter, config: WarmupConfig | None = None):
        self.rest_api = rest_api
        self.router = router
        self.config = config or WarmupConfig()

    async def load(self, symbols: Iterable[str]) -> dict[str, int]:
        result: dict[str, int] = {}
        for symbol in dict.fromkeys(s.upper().strip() for s in symbols if s.strip()):
            result[symbol] = await asyncio.to_thread(self._load_symbol, symbol)
        return result

    def _load_symbol(self, symbol: str) -> int:
        response = self.rest_api.kline_candlestick_data(
            symbol=symbol,
            interval=self.config.interval,
            limit=self.config.bars + 2,
        )
        raw = _unwrap(response)
        rows = list(raw) if isinstance(raw, (list, tuple)) else []
        if not rows:
            raise WarmupError(f"no warmup klines returned for {symbol}")
        now_ms = int(time() * 1000)
        closed: list[tuple[int, Bar]] = []
        for row in rows:
            values = _row_values(row)
            if len(values) < 7:
                continue
            close_time = int(float(values[6]))
            if close_time >= now_ms:
                continue
            closed.append((close_time, Bar(float(values[1]), float(values[2]), float(values[3]), float(values[4]), float(values[5]))))
        closed.sort(key=lambda x: x[0])
        closed = closed[-self.config.bars:]
        if len(closed) < 50:
            raise WarmupError(f"insufficient closed warmup bars for {symbol}: {len(closed)}")
        state = self.router.state(symbol)
        state.bars.clear()
        for _, bar in closed:
            state.add_bar(bar)
        return len(closed)
