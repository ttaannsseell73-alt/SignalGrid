from signalgrid.backtest.scalping_disjoint_candidate_cli import (
    FROZEN_CANDIDATE,
    _bt,
)


def test_disjoint_candidate_is_explicitly_frozen():
    assert FROZEN_CANDIDATE == {
        "setup": "LIQUIDITY_SWEEP_REJECTION",
        "direction": "LONG",
        "min_stop_bps": 24.0,
        "min_score": 0.70,
        "reward_risk": 2.0,
        "max_holding_bars": 6,
    }


def test_disjoint_stress_cost_is_stricter_than_base():
    base = _bt(False)
    stress = _bt(True)
    assert stress.taker_fee_bps > base.taker_fee_bps
    assert stress.assumed_spread_bps > base.assumed_spread_bps
    assert stress.slippage_bps > base.slippage_bps
