from signalgrid.market.state import Bar, SymbolState
from signalgrid.models import Direction, GridMode
from signalgrid.signals.scalping import (
    ScalpingConfig,
    ScalpingSignalEngine,
    compression_ratio,
    detect_scalping_structure,
    market_structure_bias,
    price_impact_efficiency,
)


def _base_state(symbol: str = "BTCUSDT") -> SymbolState:
    s = SymbolState(symbol)
    price = 100.0
    # Broad baseline first.
    for i in range(42):
        c = price + (i % 3 - 1) * 0.03
        s.add_bar(Bar(c - 0.05, c + 0.35, c - 0.35, c, 1000))
    # Then a tighter range so compression can be measured.
    for i in range(18):
        c = 100.0 + (i % 2) * 0.02
        s.add_bar(Bar(c - 0.03, c + 0.12, c - 0.12, c, 900))
    s.best_bid, s.best_ask = 99.995, 100.005
    s.taker_buy_quote, s.taker_sell_quote = 70.0, 30.0
    s.bid_depth, s.ask_depth = 65.0, 35.0
    return s


def test_compression_ratio_detects_recent_contraction():
    s = _base_state()
    ratio = compression_ratio(list(s.bars), 8, 30)
    assert ratio is not None
    assert ratio < 1.0


def test_detects_long_liquidity_sweep_without_lookahead():
    s = _base_state()
    prior_low = min(b.low for b in list(s.bars)[-12:])
    s.add_bar(Bar(100.0, 100.15, prior_low - 0.30, 99.95, 1800))
    st = detect_scalping_structure(list(s.bars), 12)
    assert st.direction == 1
    assert st.setup == "LIQUIDITY_SWEEP_REJECTION"
    assert st.invalidation is not None
    assert st.invalidation < prior_low


def test_detects_breakout_retest_on_closed_bars():
    s = _base_state()
    prior_high = max(b.high for b in list(s.bars)[-12:])
    s.add_bar(Bar(prior_high + 0.02, prior_high + 0.35, prior_high + 0.01, prior_high + 0.25, 1800))
    s.add_bar(Bar(prior_high + 0.12, prior_high + 0.28, prior_high - 0.02, prior_high + 0.18, 1700))
    st = detect_scalping_structure(list(s.bars), 12, retest_tolerance_bps=8.0)
    assert st.direction == 1
    assert st.setup == "BREAKOUT_RETEST"


def test_scalping_engine_emits_directional_signal_only():
    s = _base_state()
    prior_low = min(b.low for b in list(s.bars)[-12:])
    s.add_bar(Bar(100.0, 100.20, prior_low - 0.35, 100.08, 1800))
    s.best_bid, s.best_ask = 100.075, 100.085

    cfg = ScalpingConfig(min_score=0.50, min_directional_flow=0.01, max_stop_bps=200.0)
    sig = ScalpingSignalEngine(cfg).evaluate(s)
    assert sig.direction is Direction.LONG
    assert sig.grid_mode is GridMode.LONG_GRID
    assert sig.setup == "LIQUIDITY_SWEEP_REJECTION"
    assert sig.expires_at > sig.created_at
    assert sig.expires_at - sig.created_at <= 3.1


def test_scalping_engine_rejects_wide_spread():
    s = _base_state()
    prior_high = max(b.high for b in list(s.bars)[-12:])
    s.add_bar(Bar(100.0, prior_high + 0.4, 99.95, prior_high + 0.3, 2000))
    s.best_bid, s.best_ask = 99.9, 100.1

    sig = ScalpingSignalEngine().evaluate(s)
    assert sig.direction is Direction.PASS
    assert sig.setup == "SCALP_LIQUIDITY_GATE"


def test_scalping_engine_rejects_opposing_flow():
    s = _base_state()
    prior_low = min(b.low for b in list(s.bars)[-12:])
    s.add_bar(Bar(100.0, 100.12, prior_low - 0.35, 99.96, 1800))
    s.best_bid, s.best_ask = 99.955, 99.965
    s.taker_buy_quote, s.taker_sell_quote = 20.0, 80.0
    s.bid_depth, s.ask_depth = 30.0, 70.0

    cfg = ScalpingConfig(min_score=0.50, max_stop_bps=200.0)
    sig = ScalpingSignalEngine(cfg).evaluate(s)
    assert sig.direction is Direction.PASS
    assert sig.setup == "SCALP_FLOW_NOT_CONFIRMED"


def test_market_structure_bias_quantifies_hh_hl_and_lh_ll():
    up = []
    for i in range(5):
        up.append(Bar(100 + i, 101 + i, 99 + i, 100.5 + i, 1))
    for i in range(5):
        up.append(Bar(106 + i, 107 + i, 105 + i, 106.5 + i, 1))
    up.append(Bar(111, 112, 110, 111.5, 1))
    ms = market_structure_bias(up, 5)
    assert ms.bias == 1
    assert ms.label == "HH_HL"

    down = []
    for i in range(5):
        down.append(Bar(110 - i, 111 - i, 109 - i, 109.5 - i, 1))
    for i in range(5):
        down.append(Bar(104 - i, 105 - i, 103 - i, 103.5 - i, 1))
    down.append(Bar(99, 100, 98, 98.5, 1))
    ms = market_structure_bias(down, 5)
    assert ms.bias == -1
    assert ms.label == "LH_LL"


def test_price_impact_efficiency_flags_strong_flow_without_progress():
    bars = [
        Bar(100, 100.2, 99.8, 100.0, 1),
        Bar(100, 100.1, 99.9, 100.0, 1),
    ]
    low_eff = price_impact_efficiency(
        bars,
        reference=100.001,
        side=1,
        natr_value=0.001,
        taker_flow=0.6,
    )
    assert low_eff is not None
    assert low_eff < 0.08


def test_scalping_engine_rejects_absorbed_aggressive_flow():
    s = _base_state()
    prior_low = min(b.low for b in list(s.bars)[-12:])
    # Long liquidity sweep with strong aggressive buy flow, but almost no
    # close-to-close price progress: this is the absorption case.
    s.add_bar(Bar(100.0, 100.10, prior_low - 0.30, 100.01, 2000))
    s.best_bid, s.best_ask = 100.015, 100.025
    s.taker_buy_quote, s.taker_sell_quote = 85.0, 15.0
    s.bid_depth, s.ask_depth = 70.0, 30.0

    cfg = ScalpingConfig(
        min_score=0.45,
        max_stop_bps=250.0,
        absorption_flow_threshold=0.30,
        min_flow_price_progress_natr=0.10,
    )
    sig = ScalpingSignalEngine(cfg).evaluate(s)
    assert sig.direction is Direction.PASS
    assert sig.setup == "SCALP_ABSORPTION"


def test_historical_mode_does_not_fabricate_missing_book_microstructure():
    s = _base_state()
    prior_low = min(b.low for b in list(s.bars)[-12:])
    s.add_bar(Bar(100.0, 100.20, prior_low - 0.35, 100.08, 1800))
    s.best_bid = None
    s.best_ask = None
    s.bid_depth = 0.0
    s.ask_depth = 0.0

    live_sig = ScalpingSignalEngine(
        ScalpingConfig(min_score=0.45, max_stop_bps=250.0)
    ).evaluate(s)
    assert live_sig.direction is Direction.PASS
    assert live_sig.setup == "SCALP_LIQUIDITY_GATE"

    historical_sig = ScalpingSignalEngine(
        ScalpingConfig(
            min_score=0.45,
            max_stop_bps=250.0,
            require_book_microstructure=False,
            absorption_flow_threshold=0.95,
        )
    ).evaluate(s)
    assert historical_sig.direction is Direction.LONG
    assert historical_sig.grid_mode is GridMode.LONG_GRID


def test_scalping_engine_rejects_raw_breakout_without_confirmation():
    s = _base_state()
    prior_high = max(b.high for b in list(s.bars)[-12:])
    reference = prior_high + 0.30
    s.add_bar(Bar(100.0, reference + 0.10, 99.95, reference, 2000))
    s.best_bid, s.best_ask = reference - 0.005, reference + 0.005
    s.taker_buy_quote, s.taker_sell_quote = 80.0, 20.0
    s.bid_depth, s.ask_depth = 70.0, 30.0

    cfg = ScalpingConfig(
        min_score=0.50,
        max_stop_bps=200.0,
        compression_ratio_max=0.0,
    )
    sig = ScalpingSignalEngine(cfg).evaluate(s)
    assert sig.direction is Direction.PASS
    assert sig.setup == "SCALP_BREAKOUT_NEEDS_CONFIRMATION"
