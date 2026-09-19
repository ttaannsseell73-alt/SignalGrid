from signalgrid.market.state import Bar, SymbolState
from signalgrid.models import Direction, GridMode
from signalgrid.signals.engine import SignalEngine
from signalgrid.signals.structure import detect_structure

def make_state(direction: int = 1) -> SymbolState:
    s = SymbolState("SOLUSDT")
    price = 100.0
    for i in range(55):
        c = price + (i * 0.01)
        s.add_bar(Bar(c - 0.05, c + 0.20, c - 0.20, c, 1000))
    for i in range(8):
        c = 100.6 + i * 0.05
        s.add_bar(Bar(c - 0.2, c + 0.7, c - 0.7, c, 1500))
    if direction > 0:
        s.add_bar(Bar(100.8, 103.0, 100.4, 102.8, 5000))
        s.taker_buy_quote, s.taker_sell_quote = 70, 30
        s.bid_depth, s.ask_depth = 65, 35
        s.best_bid, s.best_ask = 102.79, 102.81
    else:
        s.add_bar(Bar(100.8, 101.0, 97.0, 97.2, 5000))
        s.taker_buy_quote, s.taker_sell_quote = 30, 70
        s.bid_depth, s.ask_depth = 35, 65
        s.best_bid, s.best_ask = 97.19, 97.21
    return s

def test_long_signal():
    sig = SignalEngine().evaluate(make_state(1))
    assert sig.direction is Direction.LONG
    assert sig.strength >= 0.60

def test_short_signal():
    sig = SignalEngine().evaluate(make_state(-1))
    assert sig.direction is Direction.SHORT
    assert sig.strength >= 0.60

def test_liquidity_gate_blocks_wide_spread():
    s = make_state(1)
    s.best_bid, s.best_ask = 100.0, 101.0
    sig = SignalEngine().evaluate(s)
    assert sig.direction is Direction.PASS
    assert sig.setup == "LIQUIDITY_GATE"


def test_balanced_compressed_range_emits_neutral_grid():
    s = SymbolState("RANGEUSDT")
    for _ in range(60):
        s.add_bar(Bar(100.0, 100.20, 99.80, 100.0, 1000))
    s.taker_buy_quote, s.taker_sell_quote = 50, 50
    s.bid_depth, s.ask_depth = 50, 50
    s.best_bid, s.best_ask = 99.99, 100.01
    sig = SignalEngine().evaluate(s)
    assert sig.direction is Direction.PASS
    assert sig.grid_mode is GridMode.NEUTRAL_GRID
    assert sig.setup == "RANGE_NEUTRAL"
    assert sig.regime == "RANGE"
    assert sig.strength >= 0.55


def test_directional_signal_with_too_close_stop_is_rejected_before_execution():
    s = make_state(1)
    structure = detect_structure(list(s.bars), 20)
    assert structure.invalidation is not None
    midpoint = structure.invalidation * 1.0001
    s.best_bid = midpoint - 0.0005
    s.best_ask = midpoint + 0.0005
    sig = SignalEngine().evaluate(s)
    assert sig.direction is Direction.PASS
    assert sig.grid_mode is GridMode.PASS
    assert sig.setup == "STOP_TOO_CLOSE"
