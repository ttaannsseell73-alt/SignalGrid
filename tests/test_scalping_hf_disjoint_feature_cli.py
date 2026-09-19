from signalgrid.backtest.scalping_hf_disjoint_feature_cli import (
    COST_GATES_BPS,
    FROZEN_HYPOTHESIS,
)


def test_frozen_hf_hypothesis_is_explicit():
    assert FROZEN_HYPOTHESIS == {
        "event": "SWEEP_HIGH_REJECT",
        "flow_bin": "FLOW_STRONG_BUY",
        "direction": "LONG",
        "lookback_bars": 24,
        "horizon_bars": 60,
        "bucket_ms": 5_000,
    }


def test_disjoint_cost_gate_is_not_four_bps_only():
    assert COST_GATES_BPS == (4.0, 8.0, 12.5)
    assert 8.0 in COST_GATES_BPS
