from time import time
from signalgrid.models import Direction, Signal
from signalgrid.risk.engine import PositionView, RiskEngine

def signal(symbol="SOLUSDT"):
    now = time()
    return Signal(symbol, Direction.LONG, 0.8, "EXPANSION", "BREAKOUT_ACCEPTANCE", 100.0, True, now + 10, now)

def test_risk_approves_normal_signal():
    d = RiskEngine().decide(signal(), [])
    assert d.approved
    assert d.leverage == 3

def test_risk_caps_positions_at_ten():
    positions = [PositionView(f"C{i}USDT", Direction.LONG, 100) for i in range(10)]
    d = RiskEngine().decide(signal(), positions)
    assert not d.approved
    assert d.reason == "MAX_POSITIONS"
