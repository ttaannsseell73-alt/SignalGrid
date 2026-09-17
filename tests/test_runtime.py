import asyncio
import json
from time import time

from signalgrid.execution.paper import PaperGridBroker
from signalgrid.market.binance_events import BinanceMarketEventRouter, EventResult
from signalgrid.market.state import Bar
from signalgrid.models import Direction, RiskDecision, Signal
from signalgrid.runtime import RuntimeConfig, RuntimeMode, SignalGridRuntime
from signalgrid.scanner import ScanResult
from signalgrid.state.store import StateStore


class DummyPublic:
    def __init__(self):
        self.calls = 0

    async def run(self, symbols, on_event, stop):
        self.calls += 1
        return None


class DummyWarmup:
    def __init__(self):
        self.calls = 0

    async def load(self, symbols):
        self.calls += 1
        return {symbol: 60 for symbol in symbols}


class DummyScanner:
    def __init__(self, result=None):
        self.result = result
        self.calls = 0

    def configure_symbols(self, symbols):
        return tuple(symbols)

    def on_event(self, event):
        self.calls += 1
        return self.result


class RecoveryExecutor:
    def __init__(self):
        self.recoveries = 0

    async def recover_after_reconciliation(self):
        self.recoveries += 1


def _state(router):
    state = router.state("SOLUSDT")
    for i in range(60):
        base = 100.0 + i * 0.01
        state.add_bar(Bar(base, base + 0.4, base - 0.4, base + 0.1, 10.0))
    state.best_bid = 100.5
    state.best_ask = 100.7
    state.bid_depth = 20.0
    state.ask_depth = 10.0
    state.last_event_time_ms = int(time() * 1000)
    return state


def _approved_result():
    now = time()
    signal = Signal(
        symbol="SOLUSDT",
        direction=Direction.LONG,
        strength=0.9,
        regime="EXPANSION",
        setup="BREAKOUT_ACCEPTANCE",
        invalidation=95.0,
        liquidity_ok=True,
        expires_at=now + 60,
        created_at=now,
    )
    decision = RiskDecision(True, "APPROVED", 100.0, 3)
    return ScanResult("SOLUSDT", signal, decision, 4, 0.2, True, "APPROVED")


def test_testnet_gate_blocks_scanner_while_reconciliation_is_closed(tmp_path):
    store = StateStore(tmp_path / "state.db")
    router = BinanceMarketEventRouter()
    _state(router)
    scanner = DummyScanner(_approved_result())
    runtime = SignalGridRuntime(
        RuntimeConfig(RuntimeMode.TESTNET, ("SOLUSDT",), str(tmp_path / "state.db")),
        store=store,
        router=router,
        scanner=scanner,
        public_transport=DummyPublic(),
        warmup=DummyWarmup(),
    )
    assert not store.execution_ready()
    runtime._process_market_event(EventResult("SOLUSDT", "bookTicker", True))
    assert scanner.calls == 0
    assert runtime.stats.campaigns_opened == 0


def test_paper_runtime_opens_only_one_campaign_for_active_symbol_and_persists_stats(tmp_path):
    store = StateStore(tmp_path / "state.db")
    router = BinanceMarketEventRouter()
    _state(router)
    scanner = DummyScanner(_approved_result())
    broker = PaperGridBroker()
    runtime = SignalGridRuntime(
        RuntimeConfig(RuntimeMode.PAPER, ("SOLUSDT",), str(tmp_path / "state.db")),
        store=store,
        router=router,
        scanner=scanner,
        public_transport=DummyPublic(),
        warmup=DummyWarmup(),
        paper_broker=broker,
    )
    event = EventResult("SOLUSDT", "bookTicker", True)
    runtime._process_market_event(event)
    runtime._process_market_event(event)
    assert broker.active_campaign("SOLUSDT") is not None
    assert runtime.stats.campaigns_opened == 1
    saved = json.loads(store.get_runtime("runtime_stats"))
    assert saved["campaigns_opened"] == 1
    assert saved["signal_to_order_median_ms"] is not None


def test_post_reconcile_runtime_recovery_is_recorded(tmp_path):
    async def scenario():
        store = StateStore(tmp_path / "state.db")
        router = BinanceMarketEventRouter()
        executor = RecoveryExecutor()
        runtime = SignalGridRuntime(
            RuntimeConfig(RuntimeMode.TESTNET, ("SOLUSDT",), str(tmp_path / "state.db")),
            store=store,
            router=router,
            scanner=DummyScanner(),
            public_transport=DummyPublic(),
            warmup=DummyWarmup(),
            campaign_executor=executor,
        )
        await runtime._post_reconcile_recovery()
        assert executor.recoveries == 1
        assert store.get_runtime("last_campaign_recovery_ms") is not None

    asyncio.run(scenario())


def test_paper_run_warms_up_and_records_clean_stop(tmp_path):
    async def scenario():
        store = StateStore(tmp_path / "state.db")
        router = BinanceMarketEventRouter()
        public = DummyPublic()
        warmup = DummyWarmup()
        runtime = SignalGridRuntime(
            RuntimeConfig(RuntimeMode.PAPER, ("SOLUSDT",), str(tmp_path / "state.db")),
            store=store,
            router=router,
            scanner=DummyScanner(),
            public_transport=public,
            warmup=warmup,
            paper_broker=PaperGridBroker(),
        )
        await runtime.run(asyncio.Event())
        assert warmup.calls == 1
        assert public.calls == 1
        assert store.get_runtime("runtime_status") == "STOPPED"
        assert store.get_runtime("runtime_mode") == "PAPER"
        assert store.get_runtime("runtime_stats") is not None

    asyncio.run(scenario())
