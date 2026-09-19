from signalgrid.backtest.bookdepth import BookDepthSnapshot
from signalgrid.backtest.scalping_bookdepth_research_cli import _depth_regime


def snap(inner, outer):
    return BookDepthSnapshot(
        symbol="BTCUSDT",
        timestamp_ms=0,
        bid_depth_1=100 * (1 + inner),
        ask_depth_1=100 * (1 - inner),
        bid_depth_5=100 * (1 + outer),
        ask_depth_5=100 * (1 - outer),
        bid_notional_1=100,
        ask_notional_1=100,
        bid_notional_5=100,
        ask_notional_5=100,
    )


def test_depth_regimes_are_directional_and_explicit():
    assert _depth_regime(snap(0.2, 0.1)) == "BID_STACKED"
    assert _depth_regime(snap(-0.2, -0.1)) == "ASK_STACKED"
    assert _depth_regime(snap(0.2, 0.0)) == "INNER_BID"
    assert _depth_regime(snap(-0.2, 0.0)) == "INNER_ASK"
    assert _depth_regime(snap(0.0, 0.0)) == "DEPTH_BALANCED"
