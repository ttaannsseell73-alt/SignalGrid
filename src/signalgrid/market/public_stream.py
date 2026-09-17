from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from signalgrid.market.binance_events import BinanceMarketEventRouter, EventResult

EventCallback = Callable[[EventResult], None]


@dataclass(frozen=True, slots=True)
class PublicStreamConfig:
    interval: str = "1m"
    symbols_per_shard: int = 20
    reconnect_delay_ms: int = 1_000
    reconnect_attempts: int = 10
    max_setup_backoff_seconds: float = 30.0
    rotate_after_seconds: float = 23 * 60 * 60

    def __post_init__(self) -> None:
        if self.symbols_per_shard < 1:
            raise ValueError("symbols_per_shard must be >= 1")
        if self.reconnect_delay_ms < 1:
            raise ValueError("reconnect_delay_ms must be >= 1")
        if not 1 <= self.reconnect_attempts <= 10:
            raise ValueError("reconnect_attempts must be between 1 and 10")
        if self.rotate_after_seconds <= 0:
            raise ValueError("rotate_after_seconds must be > 0")


def shard_symbols(symbols: Iterable[str], size: int) -> list[tuple[str, ...]]:
    if size < 1:
        raise ValueError("size must be >= 1")
    normalized = list(dict.fromkeys(s.upper().strip() for s in symbols if s.strip()))
    return [tuple(normalized[i : i + size]) for i in range(0, len(normalized), size)]


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
    """Small live transport around Binance's official USD-M Futures SDK.

    Strategy logic is deliberately absent. Each shard owns one SDK connection,
    subscribes to the three V1 public streams per symbol, and periodically
    rotates the connection before Binance's long-lived connection boundary.
    """

    def __init__(
        self,
        router: BinanceMarketEventRouter | None = None,
        config: PublicStreamConfig | None = None,
        client_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.router = router or BinanceMarketEventRouter()
        self.config = config or PublicStreamConfig()
        self._client_factory = client_factory or self._build_sdk_client

    def _build_sdk_client(self) -> Any:
        from binance_sdk_derivatives_trading_usds_futures.derivatives_trading_usds_futures import (
            ConfigurationWebSocketStreams,
            DERIVATIVES_TRADING_USDS_FUTURES_WS_STREAMS_PROD_URL,
            DerivativesTradingUsdsFutures,
        )

        cfg = ConfigurationWebSocketStreams(
            stream_url=DERIVATIVES_TRADING_USDS_FUTURES_WS_STREAMS_PROD_URL,
            reconnect_delay=self.config.reconnect_delay_ms,
            reconnect_attempts=self.config.reconnect_attempts,
        )
        return DerivativesTradingUsdsFutures(config_ws_streams=cfg)

    def _interval_value(self) -> str:
        from binance_sdk_derivatives_trading_usds_futures.websocket_streams.models import (
            KlineCandlestickStreamsIntervalEnum,
        )

        key = f"INTERVAL_{self.config.interval}"
        try:
            return KlineCandlestickStreamsIntervalEnum[key].value
        except KeyError as exc:
            raise ValueError(f"unsupported Binance kline interval: {self.config.interval}") from exc

    def handle_message(self, data: Any, on_event: EventCallback | None = None) -> EventResult | None:
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
        tasks = [
            asyncio.create_task(self._run_shard(shard, on_event, stop), name=f"binance-public-{i}")
            for i, shard in enumerate(shards)
        ]
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _run_shard(
        self,
        symbols: tuple[str, ...],
        on_event: EventCallback | None,
        stop: asyncio.Event,
    ) -> None:
        failures = 0
        while not stop.is_set():
            connection = None
            streams: list[Any] = []
            try:
                client = self._client_factory()
                connection = await client.websocket_streams.create_connection()
                interval = self._interval_value()
                for symbol in symbols:
                    streams.extend(await self._subscribe_symbol(connection, symbol, interval, on_event))
                failures = 0
                try:
                    await asyncio.wait_for(stop.wait(), timeout=self.config.rotate_after_seconds)
                except TimeoutError:
                    pass
            except asyncio.CancelledError:
                raise
            except Exception:
                failures += 1
                delay = min(
                    self.config.max_setup_backoff_seconds,
                    (self.config.reconnect_delay_ms / 1000.0) * (2 ** min(failures - 1, 8)),
                )
                try:
                    await asyncio.wait_for(stop.wait(), timeout=delay)
                except TimeoutError:
                    pass
            finally:
                for stream in streams:
                    try:
                        await stream.unsubscribe()
                    except Exception:
                        pass
                if connection is not None:
                    try:
                        await connection.close_connection(close_session=True)
                    except Exception:
                        pass

    async def _subscribe_symbol(
        self,
        connection: Any,
        symbol: str,
        interval: str,
        on_event: EventCallback | None,
    ) -> list[Any]:
        lower = symbol.lower()
        streams = [
            await connection.aggregate_trade_streams(symbol=lower),
            await connection.individual_symbol_book_ticker_streams(symbol=lower),
            await connection.kline_candlestick_streams(symbol=lower, interval=interval),
        ]
        for stream in streams:
            stream.on("message", lambda data, cb=on_event: self.handle_message(data, cb))
        return streams
