from signalgrid.backtest.scalping_hf_feature_study_cli import (
    FLOW_BINS,
    _flow_bin,
)


def test_flow_bins_are_explicit_and_cover_unit_interval():
    assert FLOW_BINS[0][0] < -1.0
    assert FLOW_BINS[-1][1] > 1.0
    assert _flow_bin(-0.5) == "FLOW_STRONG_SELL"
    assert _flow_bin(-0.2) == "FLOW_SELL"
    assert _flow_bin(0.0) == "FLOW_BALANCED"
    assert _flow_bin(0.2) == "FLOW_BUY"
    assert _flow_bin(0.5) == "FLOW_STRONG_BUY"
