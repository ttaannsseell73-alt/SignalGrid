import asyncio

from signalgrid.state.reconciliation import StateMismatchError
from signalgrid.state.store import StateStore
from signalgrid.state.user_stream import BinanceUserStreamTransport, UserStreamConfig


class FakeRest:
    def start_user_data_stream(self):
        return {"listenKey": "listen-123"}

    def keepalive_user_data_stream(self):
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

    def on(self, event, callback):
        assert event == "message"
        self.callback = callback

    async def unsubscribe(self):
        return None


class FakeConnection:
    def __init__(self):
        self.stream = FakeStream()

    async def user_data(self, listen_key):
        assert listen_key == "listen-123"
        return self.stream

    async def close_connection(self, close_session=True):
        return None


class FakeWebsocketStreams:
    def __init__(self):
        self.connection = FakeConnection()

    async def create_connection(self):
        return self.connection


class FakeClient:
    def __init__(self):
        self.rest_api = FakeRest()
        self.websocket_streams = FakeWebsocketStreams()


def _config():
    return UserStreamConfig(
        keepalive_seconds=60,
        rotate_after_seconds=60,
        reconnect_base_seconds=0.01,
        reconnect_max_seconds=0.01,
    )


def test_post_reconcile_callback_runs_before_execution_gate_opens(tmp_path):
    async def scenario():
        store = StateStore(tmp_path / "state.db")
        client = FakeClient()
        transport = BinanceUserStreamTransport(store, config=_config(), client_factory=lambda: client)
        observed_gate = []

        async def recovered():
            observed_gate.append(store.execution_ready())

        stop = asyncio.Event()
        task = asyncio.create_task(transport.run(stop, on_reconciled=recovered))
        for _ in range(200):
            if store.execution_ready():
                break
            await asyncio.sleep(0.001)
        assert store.execution_ready()
        assert observed_gate == [False]
        stop.set()
        await task

    asyncio.run(scenario())


def test_failed_post_reconcile_recovery_never_opens_gate_and_halts(tmp_path):
    async def scenario():
        store = StateStore(tmp_path / "state.db")
        client = FakeClient()
        transport = BinanceUserStreamTransport(store, config=_config(), client_factory=lambda: client)
        stop = asyncio.Event()

        async def broken_recovery():
            raise RuntimeError("missing protective order")

        try:
            await transport._run_session(stop, None, broken_recovery)
        except StateMismatchError:
            pass
        else:
            raise AssertionError("failed post-reconcile recovery must fail closed")
        assert store.halted()
        assert not store.execution_ready()
        assert (store.halt_reason() or "").startswith("POST_RECONCILE_RECOVERY_FAILED")

    asyncio.run(scenario())
