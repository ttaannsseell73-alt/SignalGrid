from signalgrid.backtest.scalping_bookdepth_disjoint_cli import (
    COST_GATES_BPS,
    FROZEN_BOOKDEPTH_HYPOTHESIS,
)


def test_frozen_bookdepth_candidate_is_explicit():
    assert FROZEN_BOOKDEPTH_HYPOTHESIS == {
        "event": "BREAKOUT_DOWN",
        "flow": "FLOW_SELL",
        "depth_regime": "BID_STACKED",
        "direction": "LONG",
        "lookback_bars": 20,
        "horizon_bars": 20,
        "bar_seconds": 30,
    }


def test_bookdepth_candidate_requires_nontrivial_cost_gate():
    assert COST_GATES_BPS == (4.0, 8.0, 12.5)
