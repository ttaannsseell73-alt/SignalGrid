from __future__ import annotations

from signalgrid.market.state import SymbolState
from signalgrid.models import Signal
from signalgrid.signals.scalping import ScalpingSignalEngine
from signalgrid.sonar.impulse_radar import ImpulseRadar, ImpulseRadarEvent


class SonarGatedSignalEngine:
    """Locked architecture adapter: Coin Sonar wakes the existing Signal Hub.

    Coin Sonar never creates LONG/SHORT trades. It only decides whether the
    downstream scalping signal engine is allowed to evaluate this market state.
    """

    def __init__(
        self,
        signal_engine: ScalpingSignalEngine,
        impulse_radar: ImpulseRadar,
    ) -> None:
        self.signal_engine = signal_engine
        self.impulse_radar = impulse_radar
        self.last_event: dict[str, ImpulseRadarEvent] = {}

    def evaluate(self, state: SymbolState) -> Signal:
        now_ms = int(state.last_event_time_ms or 0)
        wake = self.impulse_radar.observe(state, now_ms)
        if wake is not None:
            self.last_event[state.symbol] = wake
        elif not self.impulse_radar.is_awake(state.symbol, now_ms):
            return Signal.pass_signal(state.symbol, "IMPULSE_RADAR_SLEEP")
        return self.signal_engine.evaluate(state)
