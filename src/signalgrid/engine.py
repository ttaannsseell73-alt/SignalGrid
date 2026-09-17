from __future__ import annotations
from signalgrid.market.state import SymbolState
from signalgrid.risk.engine import PositionView, RiskEngine
from signalgrid.signals.engine import SignalEngine

class SignalGridEngine:
    def __init__(self, signal_engine: SignalEngine | None = None, risk_engine: RiskEngine | None = None):
        self.signals = signal_engine or SignalEngine()
        self.risk = risk_engine or RiskEngine()

    def evaluate_symbol(self, state: SymbolState, positions: list[PositionView]):
        signal = self.signals.evaluate(state)
        decision = self.risk.decide(signal, positions)
        return signal, decision
