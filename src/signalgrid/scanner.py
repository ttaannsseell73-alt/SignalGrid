from __future__ import annotations

from dataclasses import dataclass
from time import monotonic_ns, time
from typing import Callable, Iterable

from signalgrid.engine import SignalGridEngine
from signalgrid.market.binance_events import BinanceMarketEventRouter, EventResult
from signalgrid.models import Direction, GridMode, RiskDecision, Signal
from signalgrid.risk.engine import PositionView
from signalgrid.sonar.impulse_radar import ImpulseRadar, ImpulseRadarEvent

PositionsProvider = Callable[[], list[PositionView]]
ClockMs = Callable[[], int]

@dataclass(frozen=True, slots=True)
class ScannerConfig:
    max_symbols: int = 50
    min_evaluation_interval_ms: int = 100
    max_market_data_age_ms: int = 2_500
    signal_debounce_ms: int = 5_000

    def __post_init__(self) -> None:
        if not 1 <= self.max_symbols <= 50:
            raise ValueError("max_symbols must be between 1 and 50")
        if self.min_evaluation_interval_ms < 0:
            raise ValueError("min_evaluation_interval_ms must be >= 0")
        if self.max_market_data_age_ms <= 0:
            raise ValueError("max_market_data_age_ms must be > 0")
        if self.signal_debounce_ms < 0:
            raise ValueError("signal_debounce_ms must be >= 0")

@dataclass(slots=True)
class ScanResult:
    symbol: str
    signal: Signal
    decision: RiskDecision
    market_event_age_ms: int
    compute_latency_ms: float
    emitted: bool
    reason: str

class MultiSymbolScanner:
    """Event-driven scanner only; no order placement is allowed here."""

    def __init__(
        self,
        router: BinanceMarketEventRouter,
        engine: SignalGridEngine | None = None,
        config: ScannerConfig | None = None,
        positions_provider: PositionsProvider | None = None,
        now_ms: ClockMs | None = None,
        impulse_radar: ImpulseRadar | None = None,
    ) -> None:
        self.router = router
        self.engine = engine or SignalGridEngine()
        self.config = config or ScannerConfig()
        self.positions_provider = positions_provider or (lambda: [])
        self.now_ms = now_ms or (lambda: int(time() * 1000))
        self.impulse_radar = impulse_radar
        self.last_impulse_event: dict[str, ImpulseRadarEvent] = {}
        self._symbols: set[str] = set()
        self._last_eval_ms: dict[str, int] = {}
        self._last_emit_ms: dict[tuple[str, GridMode, str], int] = {}

    def configure_symbols(self, symbols: Iterable[str]) -> tuple[str, ...]:
        normalized = tuple(dict.fromkeys(s.upper().strip() for s in symbols if s.strip()))
        if not normalized:
            raise ValueError("at least one symbol is required")
        if len(normalized) > self.config.max_symbols:
            raise ValueError(f"symbol count exceeds max_symbols={self.config.max_symbols}")
        self._symbols = set(normalized)
        return normalized

    def on_event(self, event: EventResult) -> ScanResult | None:
        symbol = event.symbol.upper()
        if not event.changed or (self._symbols and symbol not in self._symbols):
            return None
        now_ms = self.now_ms()
        last_eval = self._last_eval_ms.get(symbol)
        if last_eval is not None and now_ms - last_eval < self.config.min_evaluation_interval_ms:
            return None
        self._last_eval_ms[symbol] = now_ms
        state = self.router.state(symbol)
        source_ms = state.last_event_time_ms
        if source_ms is None:
            return self._pass_result(symbol, "NO_EVENT_TIME", 0)
        event_age = max(0, now_ms - source_ms)
        if event_age > self.config.max_market_data_age_ms:
            return self._pass_result(symbol, "STALE_MARKET_DATA", event_age)
        if self.impulse_radar is not None:
            wake = self.impulse_radar.observe(state, now_ms)
            if wake is None:
                return self._pass_result(symbol, "IMPULSE_RADAR_SLEEP", event_age)
            self.last_impulse_event[symbol] = wake
        started = monotonic_ns()
        signal, decision = self.engine.evaluate_symbol(state, self.positions_provider())
        compute_ms = (monotonic_ns() - started) / 1_000_000
        emitted = False
        reason = decision.reason
        if decision.approved and signal.grid_mode is not GridMode.PASS:
            key = (symbol, signal.grid_mode, signal.setup)
            last_emit = self._last_emit_ms.get(key)
            if last_emit is not None and now_ms - last_emit < self.config.signal_debounce_ms:
                decision = RiskDecision(False, "SIGNAL_DEBOUNCE")
                reason = decision.reason
            else:
                self._last_emit_ms[key] = now_ms
                emitted = True
        return ScanResult(symbol, signal, decision, event_age, compute_ms, emitted, reason)

    def _pass_result(self, symbol: str, reason: str, event_age: int) -> ScanResult:
        signal = Signal.pass_signal(symbol, reason)
        decision = RiskDecision(False, reason)
        return ScanResult(symbol, signal, decision, event_age, 0.0, False, reason)
