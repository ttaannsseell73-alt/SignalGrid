import asyncio
import json

from signalgrid.market.public_stream import (
    USDM_MARKET_STREAM_BASE,
    USDM_PUBLIC_STREAM_BASE,
    BinancePublicStreamTransport,
    PublicStreamConfig,
    _message_to_dict,
    combined_stream_url,
    market_stream_names,
    public_stream_names,
    shard_symbols,
)


class FakeModel:
    def model_dump(self, **_kwargs):
        return {
            "e": "bookTicker",
            "s": "SOLUSDT",
            "b": "99",
            "B": "2",
            "a": "101",
            "A": "1",
        }


class FakeSocket:
    def __init__(self, messages):
        self.messages = list(messages)
        self.closed = False

    async def recv(self):
        if self.messages:
            return self.messages.pop(0)
        await asyncio.sleep(3600)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        self.closed = True


class FakeConnector:
    def __init__(self, sockets):
        self.sockets = list(sockets)
        self.urls = []

    def __call__(self, url, **_kwargs):
        self.urls.append(url)
        if not self.sockets:
            raise RuntimeError("no fake sockets left")
        return self.sockets.pop(0)


def test_shard_symbols_deduplicates_and_bounds_size():
    symbols = [f"C{i}USDT" for i in range(45)] + ["C1USDT"]
    shards = shard_symbols(symbols, 20)
    assert [len(x) for x in shards] == [20, 20, 5]
    assert sum(len(x) for x in shards) == 45


def test_2026_split_stream_routing():
    public = public_stream_names(["BTCUSDT", "ETHUSDT"])
    market = market_stream_names(["BTCUSDT", "ETHUSDT"], "1m")
    assert public == ["btcusdt@bookTicker", "ethusdt@bookTicker"]
    assert market == [
        "btcusdt@aggTrade",
        "btcusdt@kline_1m",
        "ethusdt@aggTrade",
        "ethusdt@kline_1m",
    ]
    assert combined_stream_url(USDM_PUBLIC_STREAM_BASE, public).startswith(
        "wss://fstream.binance.com/public/stream?streams="
    )
    assert combined_stream_url(USDM_MARKET_STREAM_BASE, market).startswith(
        "wss://fstream.binance.com/market/stream?streams="
    )


def test_message_normalization_and_routing():
    t = BinancePublicStreamTransport(websocket_connect=lambda *_a, **_k: None)
    event = t.handle_message(FakeModel())
    assert event.kind == "bookTicker"
    assert t.router.state("SOLUSDT").best_bid == 99.0
    assert _message_to_dict({"x": 1}) == {"x": 1}


def test_consume_connection_handles_combined_payload_and_stop():
    async def run():
        stop = asyncio.Event()
        payload = {
            "stream": "solusdt@aggTrade",
            "data": {
                "e": "aggTrade",
                "E": 60_100,
                "T": 60_100,
                "s": "SOLUSDT",
                "p": "100",
                "q": "2",
                "m": False,
            },
        }
        socket = FakeSocket([json.dumps(payload)])
        connector = FakeConnector([socket])
        transport = BinancePublicStreamTransport(
            config=PublicStreamConfig(receive_timeout_seconds=0.01),
            websocket_connect=connector,
        )

        seen = []

        def on_event(event):
            seen.append(event.kind)
            stop.set()

        await transport._consume_connection(
            "wss://example.test/market/stream?streams=solusdt@aggTrade",
            on_event,
            stop,
        )
        assert seen == ["aggTrade"]
        assert transport.router.state("SOLUSDT").taker_buy_quote == 200.0
        assert socket.closed is True

    asyncio.run(run())
