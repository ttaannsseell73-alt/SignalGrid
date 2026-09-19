from signalgrid.backtest.scalping_shadow_research_cli import (
    DEPTH_BINS,
    FLOW_BINS,
    HORIZON_SECONDS,
    MICRO_BINS,
    _bucket,
)


def test_shadow_research_design_is_bounded_and_explicit():
    assert HORIZON_SECONDS == (5, 15, 30)
    assert _bucket(-0.5, FLOW_BINS) == "FLOW_STRONG_SELL"
    assert _bucket(0.5, FLOW_BINS) == "FLOW_STRONG_BUY"
    assert _bucket(-0.5, DEPTH_BINS) == "DEPTH_ASK_HEAVY"
    assert _bucket(0.5, DEPTH_BINS) == "DEPTH_BID_HEAVY"
    assert _bucket(-0.1, MICRO_BINS) == "MICRO_ASK"
    assert _bucket(0.1, MICRO_BINS) == "MICRO_BID"
