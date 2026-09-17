from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from inspect import isawaitable
from typing import Any, Awaitable, Callable

from signalgrid.state.reconciliation import (
    AccountReconciler,
    BinanceRestSnapshotProvider,
    ReconciliationUnavailableError,
    StateMismatchError,
)
from signalgrid.state.store import StateStore
from signalgrid.state.user_data import BinanceUserDataProcessor, UserDataResult

UserEventCallback = Callable[[UserDataResult], None]
ReconciledCallback = Callable[[], Awaitable[None] | None]


@dataclass(frozen=True, slots=True)
class UserStreamConfig:
    keepalive_seconds: float = 45 * 60
    rotate_after_seconds: float = 23 * 60 * 60
    reconnect_base_seconds: float = 1.0
    reconnect_max_seconds: float = 30.0
    sdk_reconnect_delay_ms: int = 1_000
    sdk_reconnect_attempts: int = 10

    def __post_init__(self) -> None:
        if not 60 <= self.keepalive_seconds < 60 * 60:
            raise ValueError("keepalive_seconds must be between 60 and 3600 seconds")
        if self.rotate_after_seconds <= 0:
            raise ValueError("rotate_after_seconds must be positive")
        if self.reconnect_base_seconds <= 0 or self.reconnect_max_seconds <= 0:
            raise ValueError("reconnect backoff must be positive")
        if not 1 <= self.sdk_reconnect_attempts <= 10:
            raise ValueError("sdk_reconnect_attempts must be between 1 and 10")


def _model_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    data = getattr(value, "data", None)
    if callable(data):
        return _model_dict(data())
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        raw = to_dict()
        if isinstance(raw, dict):
            return raw
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(by_alias=True, exclude_none=True)
    return vars(value)


def listen_key_from_response(response: Any) -> str:
    raw = _model_dict(response)
    key = raw.get("listenKey") or raw.get("listen_key")
    if not key:
        raise ReconciliationUnavailableError("Binance start_user_data_stream returned no listenKey")
    return str(key)


class BinanceUserStreamTransport:
    """Authenticated account-state transport with fail-closed reconnect recovery.

    Session ordering:
      1) disable execution
      2) create/attach user stream
      3) buffer incoming account events
      4) REST reconcile while the stream is already attached
      5) drain buffered events in order
      6) run post-reconcile ownership/recovery checks
      7) enable execution

    The post-reconcile callback runs on every session, including reconnects,
    before the entry gate can reopen.
    """

    def __init__(
        self,
        store: StateStore,
        config: UserStreamConfig | None = None,
        client_factory: Callable[[], Any] | None = None,
        processor: BinanceUserDataProcessor | None = None,
    ) -> None:
        self.store = store
        self.config = config or UserStreamConfig()
        self.processor = processor or BinanceUserDataProcessor(store)
        self._client_factory = client_factory or self._build_sdk_client

    def _build_sdk_client(self) -> Any:
        from binance_sdk_derivatives_trading_usds_futures.derivatives_trading_usds_futures import (
            ConfigurationRestAPI,
            ConfigurationWebSocketStreams,
            DERIVATIVES_TRADING_USDS_FUTURES_REST_API_PROD_URL,
            DERIVATIVES_TRADING_USDS_FUTURES_WS_STREAMS_PROD_URL,
            DerivativesTradingUsdsFutures,
        )

        api_key = os.getenv("BINANCE_API_KEY") or os.getenv("API_KEY") or ""
        api_secret = os.getenv("BINANCE_API_SECRET") or os.getenv("API_SECRET") or ""
        if not api_key or not api_secret:
            raise RuntimeError("BINANCE_API_KEY and BINANCE_API_SECRET are required for private transport")
        rest_cfg = ConfigurationRestAPI(
            api_key=api_key,
            api_secret=api_secret,
            base_path=os.getenv("BINANCE_FUTURES_REST_URL", DERIVATIVES_TRADING_USDS_FUTURES_REST_API_PROD_URL),
        )
        ws_cfg = ConfigurationWebSocketStreams(
            stream_url=os.getenv("BINANCE_FUTURES_WS_STREAM_URL", DERIVATIVES_TRADING_USDS_FUTURES_WS_STREAMS_PROD_URL),
            reconnect_delay=self.config.sdk_reconnect_delay_ms,
            reconnect_attempts=self.config.sdk_reconnect_attempts,
        )
        return DerivativesTradingUsdsFutures(config_rest_api=rest_cfg, config_ws_streams=ws_cfg)

    async def run(
        self,
        stop_event: asyncio.Event | None = None,
        on_event: UserEventCallback | None = None,
        on_reconciled: ReconciledCallback | None = None,
    ) -> None:
        stop = stop_event or asyncio.Event()
        failures = 0
        while not stop.is_set():
            if self.store.halted():
                raise StateMismatchError(self.store.halt_reason() or "HALTED")
            try:
                reconnect = await self._run_session(stop, on_event, on_reconciled)
                failures = 0
                if stop.is_set():
                    return
                if not reconnect:
                    return
            except asyncio.CancelledError:
                raise
            except StateMismatchError:
                raise
            except Exception:
                self.store.set_execution_ready(False)
                failures += 1
                delay = min(
                    self.config.reconnect_max_seconds,
                    self.config.reconnect_base_seconds * (2 ** min(failures - 1, 8)),
                )
                try:
                    await asyncio.wait_for(stop.wait(), timeout=delay)
                except TimeoutError:
                    pass

    async def _run_session(
        self,
        stop: asyncio.Event,
        on_event: UserEventCallback | None,
        on_reconciled: ReconciledCallback | None = None,
    ) -> bool:
        self.store.set_execution_ready(False)
        self.store.set_runtime("user_stream_status", "CONNECTING")
        client = self._client_factory()
        rest_api = client.rest_api
        listen_response = await asyncio.to_thread(rest_api.start_user_data_stream)
        listen_key = listen_key_from_response(listen_response)

        connection = None
        stream = None
        keepalive_task: asyncio.Task[Any] | None = None
        session_expired = asyncio.Event()
        queue: asyncio.Queue[Any] = asyncio.Queue()
        buffering = True
        loop = asyncio.get_running_loop()

        def dispatch(data: Any) -> None:
            nonlocal buffering
            if buffering:
                queue.put_nowait(data)
                return
            result = self.processor.on_message(data)
            if on_event is not None:
                on_event(result)
            if result.reconnect_required:
                session_expired.set()

        def handle(data: Any) -> None:
            loop.call_soon_threadsafe(dispatch, data)

        try:
            connection = await client.websocket_streams.create_connection()
            stream = await connection.user_data(listen_key)
            stream.on("message", handle)
            self.store.set_runtime("user_stream_status", "RECONCILING")

            provider = BinanceRestSnapshotProvider(rest_api)
            reconciler = AccountReconciler(self.store, provider)
            try:
                snapshot = await asyncio.to_thread(provider.snapshot)
            except ReconciliationUnavailableError:
                self.store.set_execution_ready(False)
                raise
            except StateMismatchError as exc:
                self.store.halt(str(exc))
                raise
            reconciler.reconcile_snapshot(snapshot, activate=False)

            while not queue.empty():
                result = self.processor.on_message(queue.get_nowait())
                if on_event is not None:
                    on_event(result)
                if result.reconnect_required:
                    session_expired.set()
            buffering = False
            if self.store.halted():
                raise StateMismatchError(self.store.halt_reason() or "HALTED")
            if session_expired.is_set():
                self.store.set_execution_ready(False)
                return True

            if on_reconciled is not None:
                try:
                    maybe_awaitable = on_reconciled()
                    if isawaitable(maybe_awaitable):
                        await maybe_awaitable
                except StateMismatchError:
                    raise
                except Exception as exc:
                    if not self.store.halted():
                        self.store.halt(f"POST_RECONCILE_RECOVERY_FAILED:{type(exc).__name__}")
                    raise StateMismatchError(self.store.halt_reason() or "POST_RECONCILE_RECOVERY_FAILED") from exc
            if self.store.halted():
                raise StateMismatchError(self.store.halt_reason() or "HALTED")

            self.store.set_runtime("user_stream_status", "LIVE")
            self.store.set_execution_ready(True)

            keepalive_task = asyncio.create_task(
                self._keepalive(rest_api, stop), name="binance-user-keepalive"
            )
            stop_task = asyncio.create_task(stop.wait(), name="signalgrid-user-stop")
            expired_task = asyncio.create_task(session_expired.wait(), name="signalgrid-user-expired")
            rotate_task = asyncio.create_task(
                asyncio.sleep(self.config.rotate_after_seconds), name="signalgrid-user-rotate"
            )
            done, pending = await asyncio.wait(
                {keepalive_task, stop_task, expired_task, rotate_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

            if keepalive_task in done:
                exc = keepalive_task.exception()
                if exc is not None:
                    raise exc
            return not stop.is_set()
        finally:
            self.store.set_execution_ready(False)
            if not self.store.halted():
                self.store.set_runtime("user_stream_status", "DISCONNECTED")
            if keepalive_task is not None and not keepalive_task.done():
                keepalive_task.cancel()
                await asyncio.gather(keepalive_task, return_exceptions=True)
            if stream is not None:
                try:
                    await stream.unsubscribe()
                except Exception:
                    pass
            if connection is not None:
                try:
                    await connection.close_connection(close_session=True)
                except Exception:
                    pass

    async def _keepalive(self, rest_api: Any, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.config.keepalive_seconds)
                return
            except TimeoutError:
                await asyncio.to_thread(rest_api.keepalive_user_data_stream)
                self.store.set_runtime("last_user_keepalive", "ok")
