from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from time import monotonic
from typing import Any, Callable, Iterable

import websockets

from signalgrid.market.binance_events import BinanceMarketEventRouter, EventResult


EventCallback = Callable[[EventResult], None]

USDM_PUBLIC_STREAM_BASE = "wss://fstream.binance.com/public/stream?streams="
USDM_MARKET_STREAM_BASE = "wss://fstream.binance.com/market/stream?streams="


@dataclass(frozen=True, slots=True)
class PublicStreamConfig:
    interval: str = "1m"
    symbols_per_shard: int = 20
    reconnect_delay_ms: int = 1_000
    reconnect_attempts: int = 10
    max_setup_backoff_seconds: float = 30.0
    rotate_after_seconds: float = 23 * 60 * 60
    receive_timeout_seconds: float = 5.0
    public_stream_base: str = USDM_PUBLIC_STREAM_BASE
    market_stream_base: str = USDM_MARKET_STREAM_BASE

    def __post_init__(self) -> None:
        if self.symbols_per_shard < 1:
            raise ValueError("symbols_per_shard must be >= 1")
        if self.reconnect_delay_ms < 1:
            raise ValueError("reconnect_delay_ms must be >= 1")
        if not 1 <= self.reconnect_attempts <= 10:
            raise ValueError("reconnect_attempts must be between 1 and 10")
        if self.rotate_after_seconds <= 0:
            raise ValueError("rotate_after_seconds must be > 0")
        if self.receive_timeout_seconds <= 0:
            raise ValueError("receive_timeout_seconds must be > 0")


def shard_symbols(symbols: Iterable[str], size: int) -> list[tuple[str, ...]]:
    if size < 1:
        raise ValueError("size must be >= 1")
    normalized = list(dict.fromkeys(s.upper().strip() for s in symbols if s.strip()))
    return [tuple(normalized[i:i + size]) for i in range(0, len(normalized), size)]


def public_stream_names(symbols: Iterable[str]) -> list[str]:
    return [f"{symbol.lower()}@bookTicker" for symbol in symbols]


def market_stream_names(symbols: Iterable[str], interval: str) -> list[str]:
    out: list[str] = []
    for symbol in symbols:
        lower = symbol.lower()
        out.extend([f"{lower}@aggTrade", f"{lower}@kline_{interval}"])
    return out


def combined_stream_url(base: str, names: list[str]) -> str:
    if not names:
        raise ValueError("at least one stream is required")
    return base + "/".join(names)


def _message_to_dict(data: Any) -> dict[str, Any]:
    if isinstance(data, dict):
        return data
    model_dump = getattr(data, "model_dump", None)
    if callable(model_dump):
        return model_dump(by_alias=True, exclude_none=True)
    as_dict = getattr(data, "dict", None)
    if callable(as_dict):
        return as_dict(by_alias=True, exclude_none=True)
    raise TypeError(f"unsupported websocket message type: {type(data)!r}")


class BinancePublicStreamTransport:
    """USD-M Futures public market transport using Binance's 2026 split routes.

    High-frequency bookTicker is routed through /public.
    Aggregate trades and klines are routed through /market.
    """

    def __init__(
        self,
        router: BinanceMarketEventRouter | None = None,
        config: PublicStreamConfig | None = None,
        websocket_connect: Callable[..., Any] | None = None,
    ) -> None:
        self.router = router or BinanceMarketEventRouter()
        self.config = config or PublicStreamConfig()
        self._websocket_connect = websocket_connect or websockets.connect

    def handle_message(
        self,
        data: Any,
        on_event: EventCallback | None = None,
    ) -> EventResult | None:
        event = self.router.on_message(_message_to_dict(data))
        if event is not None and event.changed and on_event is not None:
            on_event(event)
        return event

    async def run(
        self,
        symbols: Iterable[str],
        on_event: EventCallback | None = None,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        shards = shard_symbols(symbols, self.config.symbols_per_shard)
        if not shards:
            raise ValueError("at least one symbol is required")
        stop = stop_event or asyncio.Event()
        tasks: list[asyncio.Task[None]] = []
        for i, shard in enumerate(shards):
            tasks.extend(
                [
                    asyncio.create_task(
                        self._run_endpoint(
                            shard,
                            "public",
                            combined_stream_url(
                                self.config.public_stream_base,
                                public_stream_names(shard),
                            ),
                            on_event,
                            stop,
                        ),
                        name=f"binance-public-{i}",
                    ),
                    asyncio.create_task(
                        self._run_endpoint(
                            shard,
                            "market",
                            combined_stream_url(
                                self.config.market_stream_base,
                                market_stream_names(shard, self.config.interval),
                            ),
                            on_event,
                            stop,
                        ),
                        name=f"binance-market-{i}",
                    ),
                ]
            )
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _run_endpoint(
        self,
        symbols: tuple[str, ...],
        endpoint_name: str,
        url: str,
        on_event: EventCallback | None,
        stop: asyncio.Event,
    ) -> None:
        failures = 0
        while not stop.is_set():
            try:
                await self._consume_connection(url, on_event, stop)
                failures = 0
                if not stop.is_set():
                    # Normal rotation; reconnect immediately.
                    continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failures += 1
                if failures >= self.config.reconnect_attempts:
                    raise RuntimeError(
                        f"{endpoint_name} websocket exceeded reconnect budget "
                        f"for {','.join(symbols)}"
                    ) from exc
                delay = min(
                    self.config.max_setup_backoff_seconds,
                    (self.config.reconnect_delay_ms / 1000.0)
                    * (2 ** min(failures - 1, 8)),
                )
                try:
                    await asyncio.wait_for(stop.wait(), timeout=delay)
                except TimeoutError:
                    pass

    async def _consume_connection(
        self,
        url: str,
        on_event: EventCallback | None,
        stop: asyncio.Event,
    ) -> None:
        started = monotonic()
        async with self._websocket_connect(
            url,
            ping_interval=20,
            ping_timeout=20,
            close_timeout=10,
            max_size=4 * 1024 * 1024,
        ) as ws:
            while not stop.is_set():
                if monotonic() - started >= self.config.rotate_after_seconds:
                    return
                try:
                    raw = await asyncio.wait_for(
                        ws.recv(),
                        timeout=self.config.receive_timeout_seconds,
                    )
                except asyncio.TimeoutError:
                    continue
                payload = json.loads(raw) if isinstance(raw, str) else json.loads(raw.decode())
                self.handle_message(payload, on_event)
