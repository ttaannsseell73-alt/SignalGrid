from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from statistics import median

from signalgrid.market.state import SymbolState


@dataclass(frozen=True, slots=True)
class ImpulseWindow:
    window_ms: int
    min_abs_move_bps: float


@dataclass(frozen=True, slots=True)
class ImpulseRadarConfig:
    windows: tuple[ImpulseWindow, ...] = (
        ImpulseWindow(60_000, 50.0),
        ImpulseWindow(300_000, 150.0),
        ImpulseWindow(600_000, 180.0),
        ImpulseWindow(900_000, 200.0),
    )
    min_baseline_seconds: int = 60
    turnover_baseline_seconds: int = 300
    min_turnover_ratio: float = 1.50
    max_spread_bps: float = 8.0
    sample_interval_ms: int = 1_000
    persistence_window_ms: int = 3_600_000
    wake_cooldown_ms: int = 2_000
    turnover_mode: str = "CUMULATIVE_WINDOW"
    require_spread: bool = True

    def __post_init__(self) -> None:
        if not self.windows:
            raise ValueError("at least one impulse window is required")
        if self.min_baseline_seconds < 1:
            raise ValueError("min_baseline_seconds must be >= 1")
        if self.turnover_baseline_seconds < self.min_baseline_seconds:
            raise ValueError("turnover_baseline_seconds must be >= min_baseline_seconds")
        if self.min_turnover_ratio <= 0:
            raise ValueError("min_turnover_ratio must be positive")
        if self.max_spread_bps <= 0:
            raise ValueError("max_spread_bps must be positive")
        if self.sample_interval_ms < 100:
            raise ValueError("sample_interval_ms must be >= 100")
        if self.turnover_mode not in {"CUMULATIVE_WINDOW", "BUCKET_TOTAL"}:
            raise ValueError("turnover_mode must be CUMULATIVE_WINDOW or BUCKET_TOTAL")


@dataclass(frozen=True, slots=True)
class ImpulseRadarEvent:
    symbol: str
    event_time_ms: int
    direction: int
    window_ms: int
    move_bps: float
    threshold_bps: float
    turnover_quote: float
    adaptive_expected_turnover_quote: float
    turnover_ratio: float
    flow_imbalance: float
    spread_bps: float
    impulse_count_60m: int
    time_since_previous_impulse_ms: int | None
    mean_gap_ms_60m: float | None
    cumulative_displacement_bps_60m: float
    cumulative_turnover_quote_60m: float
    accelerating: bool


@dataclass(slots=True)
class _Sample:
    time_ms: int
    mid: float
    cumulative_turnover: float


@dataclass(slots=True)
class _SymbolRadarState:
    samples: deque[_Sample] = field(default_factory=deque)
    last_raw_turnover: float = 0.0
    cumulative_turnover: float = 0.0
    last_sample_bucket: int | None = None
    impulses: deque[ImpulseRadarEvent] = field(default_factory=deque)
    last_wake_ms: int | None = None


class ImpulseRadar:
    """Coin Sonar V2 trigger layer.

    This class never returns LONG/SHORT trade instructions. It only emits a
    symbol wake event for the existing Signal Hub when an adaptive price x
    turnover impulse survives liquidity and persistence checks.
    """

    def __init__(self, config: ImpulseRadarConfig | None = None) -> None:
        self.config = config or ImpulseRadarConfig()
        self._states: dict[str, _SymbolRadarState] = {}

    def observe(self, state: SymbolState, now_ms: int) -> ImpulseRadarEvent | None:
        has_book = (
            state.best_bid is not None
            and state.best_ask is not None
            and state.best_bid > 0
            and state.best_ask >= state.best_bid
        )
        if has_book:
            assert state.best_bid is not None and state.best_ask is not None
            mid = (state.best_bid + state.best_ask) / 2.0
            spread_bps = (state.best_ask - state.best_bid) / mid * 10_000.0
            if spread_bps > self.config.max_spread_bps:
                return None
        else:
            if self.config.require_spread or not state.bars or state.bars[-1].close <= 0:
                return None
            mid = state.bars[-1].close
            # Explicit sentinel: historical kline archives do not contain spread.
            spread_bps = -1.0

        symbol_state = self._states.setdefault(state.symbol, _SymbolRadarState())
        raw_turnover = max(0.0, state.taker_buy_quote + state.taker_sell_quote)
        if self.config.turnover_mode == "BUCKET_TOTAL":
            delta = raw_turnover
        elif raw_turnover >= symbol_state.last_raw_turnover:
            delta = raw_turnover - symbol_state.last_raw_turnover
        else:
            # Binance live flow buckets reset; preserve cumulative turnover without
            # treating the reset as negative activity.
            delta = raw_turnover
        symbol_state.last_raw_turnover = raw_turnover
        symbol_state.cumulative_turnover += max(0.0, delta)

        sample_bucket = now_ms - (now_ms % self.config.sample_interval_ms)
        if symbol_state.last_sample_bucket == sample_bucket:
            if symbol_state.samples:
                symbol_state.samples[-1] = _Sample(
                    sample_bucket,
                    mid,
                    symbol_state.cumulative_turnover,
                )
        else:
            symbol_state.samples.append(
                _Sample(sample_bucket, mid, symbol_state.cumulative_turnover)
            )
            symbol_state.last_sample_bucket = sample_bucket

        max_window = max(
            max(w.window_ms for w in self.config.windows),
            self.config.turnover_baseline_seconds * 1_000,
            self.config.persistence_window_ms,
        )
        cutoff = now_ms - max_window - self.config.sample_interval_ms
        while symbol_state.samples and symbol_state.samples[0].time_ms < cutoff:
            symbol_state.samples.popleft()

        self._prune_impulses(symbol_state, now_ms)

        if len(symbol_state.samples) < 2:
            return None
        span_ms = symbol_state.samples[-1].time_ms - symbol_state.samples[0].time_ms
        if span_ms < self.config.min_baseline_seconds * 1_000:
            return None

        baseline_rate = self._adaptive_quote_rate(symbol_state.samples, now_ms)
        if baseline_rate <= 0:
            return None

        flow_total = state.taker_buy_quote + state.taker_sell_quote
        flow_imbalance = (
            (state.taker_buy_quote - state.taker_sell_quote) / flow_total
            if flow_total > 0
            else 0.0
        )

        candidates: list[tuple[float, ImpulseWindow, float, float, float]] = []
        for window in self.config.windows:
            anchor = self._anchor(symbol_state.samples, now_ms - window.window_ms)
            if anchor is None or anchor.mid <= 0:
                continue
            move_bps = (mid - anchor.mid) / anchor.mid * 10_000.0
            actual_turnover = max(
                0.0,
                symbol_state.cumulative_turnover - anchor.cumulative_turnover,
            )
            expected = baseline_rate * (window.window_ms / 1_000.0)
            turnover_ratio = actual_turnover / expected if expected > 0 else 0.0
            if abs(move_bps) < window.min_abs_move_bps:
                continue
            if turnover_ratio < self.config.min_turnover_ratio:
                continue
            normalized = abs(move_bps) / window.min_abs_move_bps
            candidates.append(
                (normalized * turnover_ratio, window, move_bps, actual_turnover, expected)
            )

        if not candidates:
            return None

        if (
            symbol_state.last_wake_ms is not None
            and now_ms - symbol_state.last_wake_ms < self.config.wake_cooldown_ms
        ):
            return None

        _, window, move_bps, actual_turnover, expected = max(
            candidates,
            key=lambda item: item[0],
        )
        previous = symbol_state.impulses[-1] if symbol_state.impulses else None
        gaps = [
            right.event_time_ms - left.event_time_ms
            for left, right in zip(symbol_state.impulses, list(symbol_state.impulses)[1:])
        ]
        current_gap = (
            now_ms - previous.event_time_ms
            if previous is not None
            else None
        )
        mean_gap = (sum(gaps) / len(gaps)) if gaps else None
        accelerating = bool(
            previous is not None
            and current_gap is not None
            and mean_gap is not None
            and current_gap < mean_gap
            and abs(move_bps) >= abs(previous.move_bps)
        )

        event = ImpulseRadarEvent(
            symbol=state.symbol,
            event_time_ms=now_ms,
            direction=1 if move_bps > 0 else -1,
            window_ms=window.window_ms,
            move_bps=move_bps,
            threshold_bps=window.min_abs_move_bps,
            turnover_quote=actual_turnover,
            adaptive_expected_turnover_quote=expected,
            turnover_ratio=actual_turnover / expected,
            flow_imbalance=flow_imbalance,
            spread_bps=spread_bps,
            impulse_count_60m=len(symbol_state.impulses) + 1,
            time_since_previous_impulse_ms=current_gap,
            mean_gap_ms_60m=mean_gap,
            cumulative_displacement_bps_60m=(
                sum(item.move_bps for item in symbol_state.impulses) + move_bps
            ),
            cumulative_turnover_quote_60m=(
                sum(item.turnover_quote for item in symbol_state.impulses)
                + actual_turnover
            ),
            accelerating=accelerating,
        )
        symbol_state.impulses.append(event)
        symbol_state.last_wake_ms = now_ms
        return event

    def is_awake(self, symbol: str, now_ms: int) -> bool:
        """Keep Signal Hub active for one shortest-horizon Sonar window.

        The wake event is the trigger; this bounded hold lets the existing
        Signal Hub observe the immediate post-impulse confirmation/retest
        without turning Coin Sonar into a trade-direction engine.
        """
        symbol_state = self._states.get(symbol.upper())
        if symbol_state is None or symbol_state.last_wake_ms is None:
            return False
        elapsed = now_ms - symbol_state.last_wake_ms
        hold_ms = min(window.window_ms for window in self.config.windows)
        return 0 <= elapsed <= hold_ms

    def _adaptive_quote_rate(self, samples: deque[_Sample], now_ms: int) -> float:
        cutoff = now_ms - self.config.turnover_baseline_seconds * 1_000
        baseline = [sample for sample in samples if sample.time_ms >= cutoff]
        if len(baseline) < 2:
            return 0.0
        rates: list[float] = []
        for left, right in zip(baseline, baseline[1:]):
            dt = (right.time_ms - left.time_ms) / 1_000.0
            if dt <= 0:
                continue
            dq = right.cumulative_turnover - left.cumulative_turnover
            if dq >= 0:
                rates.append(dq / dt)
        return median(rates) if rates else 0.0

    @staticmethod
    def _anchor(samples: deque[_Sample], target_ms: int) -> _Sample | None:
        anchor: _Sample | None = None
        for sample in samples:
            if sample.time_ms <= target_ms:
                anchor = sample
            else:
                break
        return anchor

    def _prune_impulses(self, state: _SymbolRadarState, now_ms: int) -> None:
        cutoff = now_ms - self.config.persistence_window_ms
        while state.impulses and state.impulses[0].event_time_ms < cutoff:
            state.impulses.popleft()
