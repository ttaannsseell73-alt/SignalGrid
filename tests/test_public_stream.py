import asyncio
from signalgrid.market.public_stream import BinancePublicStreamTransport, PublicStreamConfig, _message_to_dict, shard_symbols

class FakeModel:
    def model_dump(self, **_kwargs):
        return {"e": "bookTicker", "s": "SOLUSDT", "b": "99", "B": "2", "a": "101", "A": "1"}

class FakeStream:
    def __init__(self):
        self.callback = None
        self.unsubscribed = False
    def on(self, event, callback):
        assert event == "message"
        self.callback = callback
    async def unsubscribe(self):
        self.unsubscribed = True

class FakeConnection:
    def __init__(self, stop):
        self.stop = stop
        self.streams = []
        self.closed = False
        self.calls = []
    async def aggregate_trade_streams(self, symbol):
        return self._stream("agg", symbol)
    async def individual_symbol_book_ticker_streams(self, symbol):
        return self._stream("book", symbol)
    async def kline_candlestick_streams(self, symbol, interval):
        stream = self._stream("kline", symbol, interval)
        self.stop.set()
        return stream
    def _stream(self, *call):
        self.calls.append(call)
        stream = FakeStream()
        self.streams.append(stream)
        return stream
    async def close_connection(self, close_session=True):
        assert close_session
        self.closed = True

class FakeWs:
    def __init__(self, connection):
        self.connection = connection
    async def create_connection(self):
        return self.connection

class FakeClient:
    def __init__(self, connection):
        self.websocket_streams = FakeWs(connection)

def test_shard_symbols_deduplicates_and_bounds_size():
    symbols = [f"C{i}USDT" for i in range(45)] + ["C1USDT"]
    shards = shard_symbols(symbols, 20)
    assert [len(x) for x in shards] == [20, 20, 5]
    assert sum(len(x) for x in shards) == 45

def test_message_normalization_and_routing():
    t = BinancePublicStreamTransport(client_factory=lambda: None)
    event = t.handle_message(FakeModel())
    assert event.kind == "bookTicker"
    assert t.router.state("SOLUSDT").best_bid == 99.0
    assert _message_to_dict({"x": 1}) == {"x": 1}

def test_one_shard_subscribes_three_streams_and_closes_cleanly(monkeypatch):
    async def run():
        stop = asyncio.Event()
        connection = FakeConnection(stop)
        cfg = PublicStreamConfig(symbols_per_shard=20, rotate_after_seconds=60)
        transport = BinancePublicStreamTransport(config=cfg, client_factory=lambda: FakeClient(connection))
        monkeypatch.setattr(transport, "_interval_value", lambda: "1m")
        await transport.run(["SOLUSDT"], stop_event=stop)
        assert connection.calls == [("agg", "solusdt"), ("book", "solusdt"), ("kline", "solusdt", "1m")]
        assert all(s.unsubscribed for s in connection.streams)
        assert connection.closed
    asyncio.run(run())
