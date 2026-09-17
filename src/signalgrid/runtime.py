from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections import deque
from dataclasses import dataclass
from enum import Enum
from statistics import median
from time import monotonic, time
from typing import Any, Iterable

from signalgrid.engine import SignalGridEngine
from signalgrid.execution.binance import BinanceRestExecutionAdapter
from signalgrid.execution.campaign import GridCampaignExecutor, GridCampaignRegistry
from signalgrid.execution.grid import GridConfig, build_grid_plan
from signalgrid.execution.paper import PaperGridBroker
from signalgrid.market.binance_events import BinanceMarketEventRouter, EventResult
from signalgrid.market.public_stream import BinancePublicStreamTransport
from signalgrid.market.warmup import BinanceKlineWarmup, WarmupConfig
from signalgrid.models import Direction
from signalgrid.risk.engine import PositionView
from signalgrid.scanner import MultiSymbolScanner, ScannerConfig
from signalgrid.state.store import StateStore
from signalgrid.state.user_data import UserDataResult
from signalgrid.state.user_stream import BinanceUserStreamTransport


class RuntimeMode(str, Enum):
    PAPER = "PAPER"
    TESTNET = "TESTNET"


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    mode: RuntimeMode
    symbols: tuple[str, ...]
    db_path: str = "signalgrid.db"
    startup_timeout_seconds: float = 30.0
    cleanup_interval_seconds: float = 1.0
    cleanup_grace_seconds: float = 5.0
    warmup_bars: int = 60

    def __post_init__(self) -> None:
        if not self.symbols:
            raise ValueError("at least one symbol is required")
        if len(self.symbols) > 50:
            raise ValueError("runtime supports at most 50 symbols")
        if self.startup_timeout_seconds <= 0 or self.cleanup_interval_seconds <= 0:
            raise ValueError("runtime timing values must be positive")
        if self.warmup_bars < 50:
            raise ValueError("runtime warmup requires at least 50 closed bars")


class RuntimeStats:
    def __init__(self) -> None:
        self.market_events = 0
        self.evaluations = 0
        self.signals_emitted = 0
        self.campaigns_opened = 0
        self.campaigns_closed = 0
        self.open_failures = 0
        self.cleanups = 0
        self._latencies_ms: deque[float] = deque(maxlen=2_000)

    def add_latency(self, value: float) -> None:
        self._latencies_ms.append(max(0.0, float(value)))

    def snapshot(self) -> dict[str, float | int | None]:
        values = sorted(self._latencies_ms)
        p95 = values[min(len(values) - 1, int(len(values) * 0.95))] if values else None
        return {
            "market_events": self.market_events,
            "evaluations": self.evaluations,
            "signals_emitted": self.signals_emitted,
            "campaigns_opened": self.campaigns_opened,
            "campaigns_closed": self.campaigns_closed,
            "open_failures": self.open_failures,
            "cleanups": self.cleanups,
            "signal_to_order_median_ms": median(values) if values else None,
            "signal_to_order_p95_ms": p95,
        }


class SignalGridRuntime:
    def __init__(
        self,
        config: RuntimeConfig,
        *,
        store: StateStore,
        router: BinanceMarketEventRouter,
        scanner: MultiSymbolScanner,
        public_transport: BinancePublicStreamTransport,
        warmup: BinanceKlineWarmup,
        paper_broker: PaperGridBroker | None = None,
        campaign_executor: GridCampaignExecutor | None = None,
        user_transport: BinanceUserStreamTransport | None = None,
        grid_config: GridConfig | None = None,
    ) -> None:
        self.config = config
        self.store = store
        self.router = router
        self.scanner = scanner
        self.public_transport = public_transport
        self.warmup = warmup
        self.paper_broker = paper_broker
        self.campaign_executor = campaign_executor
        self.user_transport = user_transport
        self.grid_config = grid_config or GridConfig()
        self.stats = RuntimeStats()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._opening_symbols: set[str] = set()

    @classmethod
    def from_environment(cls, config: RuntimeConfig) -> "SignalGridRuntime":
        from binance_common.configuration import ConfigurationRestAPI, ConfigurationWebSocketStreams
        from binance_common.constants import (
            DERIVATIVES_TRADING_USDS_FUTURES_REST_API_PROD_URL,
            DERIVATIVES_TRADING_USDS_FUTURES_REST_API_TESTNET_URL,
            DERIVATIVES_TRADING_USDS_FUTURES_WS_STREAMS_PROD_URL,
            DERIVATIVES_TRADING_USDS_FUTURES_WS_STREAMS_TESTNET_URL,
        )
        from binance_sdk_derivatives_trading_usds_futures.derivatives_trading_usds_futures import (
            DerivativesTradingUsdsFutures,
        )

        store = StateStore(config.db_path)
        router = BinanceMarketEventRouter()
        symbols = tuple(dict.fromkeys(s.upper() for s in config.symbols))

        if config.mode is RuntimeMode.PAPER:
            rest_cfg = ConfigurationRestAPI(base_path=DERIVATIVES_TRADING_USDS_FUTURES_REST_API_PROD_URL)
            rest_client = DerivativesTradingUsdsFutures(config_rest_api=rest_cfg)

            def public_factory() -> Any:
                ws_cfg = ConfigurationWebSocketStreams(stream_url=DERIVATIVES_TRADING_USDS_FUTURES_WS_STREAMS_PROD_URL)
                return DerivativesTradingUsdsFutures(config_ws_streams=ws_cfg)

            broker = PaperGridBroker()
            scanner = MultiSymbolScanner(
                router,
                SignalGridEngine(),
                ScannerConfig(),
                positions_provider=broker.position_views,
            )
            public = BinancePublicStreamTransport(router, client_factory=public_factory)
            warmup = BinanceKlineWarmup(rest_client.rest_api, router, WarmupConfig(bars=config.warmup_bars))
            return cls(config, store=store, router=router, scanner=scanner, public_transport=public, warmup=warmup, paper_broker=broker)

        api_key = os.getenv("BINANCE_TESTNET_API_KEY") or os.getenv("BINANCE_API_KEY") or ""
        api_secret = os.getenv("BINANCE_TESTNET_API_SECRET") or os.getenv("BINANCE_API_SECRET") or ""
        if not api_key or not api_secret:
            raise RuntimeError("TESTNET mode requires BINANCE_TESTNET_API_KEY and BINANCE_TESTNET_API_SECRET")
        rest_cfg = ConfigurationRestAPI(api_key=api_key, api_secret=api_secret, base_path=DERIVATIVES_TRADING_USDS_FUTURES_REST_API_TESTNET_URL)
        rest_client = DerivativesTradingUsdsFutures(config_rest_api=rest_cfg)

        def public_factory() -> Any:
            ws_cfg = ConfigurationWebSocketStreams(stream_url=DERIVATIVES_TRADING_USDS_FUTURES_WS_STREAMS_TESTNET_URL)
            return DerivativesTradingUsdsFutures(config_ws_streams=ws_cfg)

        def user_factory() -> Any:
            rcfg = ConfigurationRestAPI(api_key=api_key, api_secret=api_secret, base_path=DERIVATIVES_TRADING_USDS_FUTURES_REST_API_TESTNET_URL)
            wcfg = ConfigurationWebSocketStreams(stream_url=DERIVATIVES_TRADING_USDS_FUTURES_WS_STREAMS_TESTNET_URL)
            return DerivativesTradingUsdsFutures(config_rest_api=rcfg, config_ws_streams=wcfg)

        def positions() -> list[PositionView]:
            result: list[PositionView] = []
            for p in store.list_account_positions():
                direction = Direction.LONG if p.direction == "LONG" else Direction.SHORT
                result.append(PositionView(p.symbol, direction, float(p.notional_usdt)))
            return result

        def price_provider(symbol: str) -> float:
            state = router.state(symbol)
            if state.best_bid and state.best_ask:
                return (state.best_bid + state.best_ask) / 2.0
            if state.bars:
                return state.bars[-1].close
            raise RuntimeError(f"price unavailable for {symbol}")

        adapter = BinanceRestExecutionAdapter(rest_client.rest_api, price_provider, execution_gate=store.execution_ready)
        executor = GridCampaignExecutor(adapter, store)
        scanner = MultiSymbolScanner(router, SignalGridEngine(), ScannerConfig(), positions_provider=positions)
        public = BinancePublicStreamTransport(router, client_factory=public_factory)
        warmup = BinanceKlineWarmup(rest_client.rest_api, router, WarmupConfig(bars=config.warmup_bars))
        user = BinanceUserStreamTransport(store, client_factory=user_factory)
        return cls(config, store=store, router=router, scanner=scanner, public_transport=public, warmup=warmup, campaign_executor=executor, user_transport=user)

    async def run(self, stop_event: asyncio.Event | None = None) -> None:
        self._loop = asyncio.get_running_loop()
        stop = stop_event or asyncio.Event()
        symbols = self.scanner.configure_symbols(self.config.symbols)
        self.store.set_runtime("runtime_mode", self.config.mode.value)
        self.store.set_runtime("runtime_status", "WARMING_UP")
        await self.warmup.load(symbols)
        self.store.set_runtime("runtime_status", "STARTING")

        if self.config.mode is RuntimeMode.PAPER:
            self.store.set_runtime("runtime_status", "PAPER_LIVE")
            try:
                await self.public_transport.run(symbols, self._market_callback, stop)
            finally:
                self._persist_stats()
                self.store.set_runtime("runtime_status", "HALTED" if self.store.halted() else "STOPPED")
            return

        if self.user_transport is None or self.campaign_executor is None:
            raise RuntimeError("TESTNET runtime components are incomplete")
        user_task = asyncio.create_task(
            self.user_transport.run(stop, self._user_callback, self._post_reconcile_recovery),
            name="signalgrid-user",
        )
        try:
            await self._wait_execution_ready(user_task, stop)
            cleanup_task = asyncio.create_task(self._cleanup_loop(stop), name="signalgrid-grid-cleanup")
            public_task = asyncio.create_task(self.public_transport.run(symbols, self._market_callback, stop), name="signalgrid-public")
            self.store.set_runtime("runtime_status", "TESTNET_LIVE")
            done, pending = await asyncio.wait({user_task, cleanup_task, public_task}, return_when=asyncio.FIRST_EXCEPTION)
            for task in done:
                exc = task.exception() if not task.cancelled() else None
                if exc is not None:
                    raise exc
            if not stop.is_set():
                raise RuntimeError("runtime task ended unexpectedly")
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        finally:
            stop.set()
            if not user_task.done():
                user_task.cancel()
                await asyncio.gather(user_task, return_exceptions=True)
            self._persist_stats()
            self.store.set_runtime("runtime_status", "STOPPED" if not self.store.halted() else "HALTED")

    async def _post_reconcile_recovery(self) -> None:
        if self.campaign_executor is None:
            return
        await self.campaign_executor.recover_after_reconciliation()
        self.store.set_runtime("last_campaign_recovery_ms", int(time() * 1000))

    async def _wait_execution_ready(self, user_task: asyncio.Task[Any], stop: asyncio.Event) -> None:
        deadline = monotonic() + self.config.startup_timeout_seconds
        while monotonic() < deadline and not stop.is_set():
            if self.store.halted():
                raise RuntimeError(self.store.halt_reason() or "runtime halted")
            if user_task.done():
                exc = user_task.exception()
                raise RuntimeError("user stream ended before execution became ready") from exc
            if self.store.execution_ready():
                return
            await asyncio.sleep(0.05)
        raise TimeoutError("timed out waiting for reconciled TESTNET account state")

    def _market_callback(self, event: EventResult) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._process_market_event, event)

    def _process_market_event(self, event: EventResult) -> None:
        self.stats.market_events += 1
        state = self.router.state(event.symbol)
        if self.config.mode is RuntimeMode.PAPER and self.paper_broker is not None:
            active_before = self.paper_broker.active_campaign(state.symbol)
            updated = self.paper_broker.on_state(state)
            if active_before is not None and updated is not None and updated.status == "CLOSED":
                self.stats.campaigns_closed += 1
                self._persist_stats()
        if self.config.mode is RuntimeMode.TESTNET and not self.store.execution_ready():
            return
        result = self.scanner.on_event(event)
        if result is None:
            return
        self.stats.evaluations += 1
        if not result.emitted:
            return
        self.stats.signals_emitted += 1
        try:
            plan = build_grid_plan(result.signal, result.decision, state, self.grid_config)
        except Exception:
            self.stats.open_failures += 1
            return
        if self.config.mode is RuntimeMode.PAPER:
            if self.paper_broker is None or self.paper_broker.active_campaign(plan.symbol) is not None:
                return
            started = monotonic()
            self.paper_broker.open_campaign(plan)
            self.stats.campaigns_opened += 1
            self.stats.add_latency(result.market_event_age_ms + (monotonic() - started) * 1000.0)
            self._persist_stats()
            return
        if self.campaign_executor is None or plan.symbol in self._opening_symbols:
            return
        if GridCampaignRegistry(self.store).active(plan.symbol) is not None:
            return
        self._opening_symbols.add(plan.symbol)
        asyncio.create_task(self._open_testnet(plan, result.market_event_age_ms), name=f"grid-open-{plan.symbol}")

    async def _open_testnet(self, plan, event_age_ms: int) -> None:
        started = monotonic()
        try:
            assert self.campaign_executor is not None
            await self.campaign_executor.open_campaign(plan)
            self.stats.campaigns_opened += 1
            self.stats.add_latency(event_age_ms + (monotonic() - started) * 1000.0)
        except Exception:
            self.stats.open_failures += 1
        finally:
            self._opening_symbols.discard(plan.symbol)
            self._persist_stats()

    def _user_callback(self, result: UserDataResult) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(lambda: asyncio.create_task(self._handle_user_event(result)))

    async def _handle_user_event(self, result: UserDataResult) -> None:
        if self.campaign_executor is None or not result.changed:
            return
        if result.kind in {"ACCOUNT_UPDATE", "ALGO_UPDATE", "ORDER_TRADE_UPDATE"}:
            await self._cleanup_once()

    async def _cleanup_loop(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            await self._cleanup_once()
            self._persist_stats()
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.config.cleanup_interval_seconds)
            except TimeoutError:
                pass

    async def _cleanup_once(self) -> None:
        if self.campaign_executor is None:
            return
        now_ms = int(time() * 1000)
        for record in GridCampaignRegistry(self.store).records():
            if record.status not in {"ACTIVE", "DEGRADED"}:
                continue
            if now_ms - record.updated_at_ms < int(self.config.cleanup_grace_seconds * 1000):
                continue
            if await self.campaign_executor.cleanup_if_flat(record.symbol):
                self.stats.cleanups += 1
                self.stats.campaigns_closed += 1

    def _persist_stats(self) -> None:
        self.store.set_runtime("runtime_stats", json.dumps(self.stats.snapshot(), sort_keys=True))


def _parse_symbols(text: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(x.strip().upper() for x in text.split(",") if x.strip()))


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SignalGrid PAPER/TESTNET runtime")
    parser.add_argument("--mode", choices=("paper", "testnet"), required=True)
    parser.add_argument("--symbols", required=True, help="comma-separated Binance USD-M symbols")
    parser.add_argument("--db", default="signalgrid.db")
    args = parser.parse_args(list(argv) if argv is not None else None)
    mode = RuntimeMode.PAPER if args.mode == "paper" else RuntimeMode.TESTNET
    runtime = SignalGridRuntime.from_environment(RuntimeConfig(mode, _parse_symbols(args.symbols), args.db))
    try:
        asyncio.run(runtime.run())
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
