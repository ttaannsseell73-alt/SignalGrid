import asyncio

from signalgrid.state.store import StateStore
from signalgrid.state.user_stream import BinanceUserStreamTransport, UserStreamConfig, listen_key_from_response


class FakeRest:
    def __init__(self):
        self.keepalives = 0
    def start_user_data_stream(self):
        return {"listenKey": "listen-123"}
    def keepalive_user_data_stream(self):
        self.keepalives += 1
        return {}
    def current_all_open_orders(self):
        return []
    def current_all_algo_open_orders(self):
        return []
    def position_information_v3(self):
        return []


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
    def __init__(self):
        self.stream = FakeStream()
        self.listen_key = None
        self.closed = False
    async def user_data(self, listen_key):
        self.listen_key = listen_key
        return self.stream
    async def close_connection(self, close_session=True):
        self.closed = True


class FakeWebsocketStreams:
    def __init__(self, connection):
        self.connection = connection
    async def create_connection(self):
        return self.connection


class FakeClient:
    def __init__(self):
        self.rest_api = FakeRest()
        self.connection = FakeConnection()
        self.websocket_streams = FakeWebsocketStreams(self.connection)


def test_listen_key_unwraps_dict():
    assert listen_key_from_response({"listenKey": "abc"}) == "abc"


def test_session_subscribes_reconciles_and_closes_cleanly(tmp_path):
    async def scenario():
        store = StateStore(tmp_path / "state.db")
        client = FakeClient()
        transport = BinanceUserStreamTransport(
            store,
            config=UserStreamConfig(keepalive_seconds=60, rotate_after_seconds=60, reconnect_base_seconds=0.01, reconnect_max_seconds=0.01),
            client_factory=lambda: client,
        )
        stop = asyncio.Event()
        task = asyncio.create_task(transport.run(stop))
        for _ in range(100):
            if store.execution_ready():
                break
            await asyncio.sleep(0.001)
        assert store.execution_ready()
        assert client.connection.listen_key == "listen-123"
        assert store.get_runtime("last_reconcile_ms") is not None
        stop.set()
        await task
        assert client.connection.stream.unsubscribed
        assert client.connection.closed
        assert not store.execution_ready()
    asyncio.run(scenario())


def test_live_listen_key_expiry_forces_session_reconnect(tmp_path):
    async def scenario():
        store = StateStore(tmp_path / "state.db")
        client = FakeClient()
        transport = BinanceUserStreamTransport(
            store,
            config=UserStreamConfig(keepalive_seconds=60, rotate_after_seconds=60, reconnect_base_seconds=0.01, reconnect_max_seconds=0.01),
            client_factory=lambda: client,
        )
        stop = asyncio.Event()
        session = asyncio.create_task(transport._run_session(stop, None))
        for _ in range(100):
            if store.execution_ready() and client.connection.stream.callback is not None:
                break
            await asyncio.sleep(0.001)
        client.connection.stream.callback({"e": "listenKeyExpired", "E": 5000})
        reconnect = await session
        assert reconnect is True
        assert not store.execution_ready()
        assert not store.halted()
    asyncio.run(scenario())


def test_sdk_callback_from_worker_thread_is_marshaled_to_store_owner_loop(tmp_path):
    async def scenario():
        store = StateStore(tmp_path / "state.db")
        client = FakeClient()
        transport = BinanceUserStreamTransport(
            store,
            config=UserStreamConfig(keepalive_seconds=60, rotate_after_seconds=60, reconnect_base_seconds=0.01, reconnect_max_seconds=0.01),
            client_factory=lambda: client,
        )
        stop = asyncio.Event()
        task = asyncio.create_task(transport.run(stop))
        for _ in range(100):
            if store.execution_ready() and client.connection.stream.callback is not None:
                break
            await asyncio.sleep(0.001)
        event = {
            "e": "ACCOUNT_UPDATE",
            "E": 7000,
            "T": 7000,
            "a": {"P": [{"s": "SOLUSDT", "pa": "2", "ep": "100", "ps": "BOTH"}]},
        }
        await asyncio.to_thread(client.connection.stream.callback, event)
        for _ in range(100):
            if store.get_account_position("SOLUSDT") is not None:
                break
            await asyncio.sleep(0.001)
        pos = store.get_account_position("SOLUSDT")
        assert pos is not None and str(pos.quantity) == "2"
        stop.set()
        await task
    asyncio.run(scenario())
