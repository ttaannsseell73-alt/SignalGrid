from signalgrid.backtest.scalping_hf_absorption_cli import (
    FLOW_THRESHOLDS,
    LOOKBACKS,
    SweepAbsorptionEngine,
)


def test_absorption_matrix_is_small_and_predeclared():
    assert FLOW_THRESHOLDS == (0.10, 0.20, 0.30)
    assert LOOKBACKS == (12, 24)
    assert len(FLOW_THRESHOLDS) * len(LOOKBACKS) == 6


def test_absorption_engine_keeps_opposing_flow_threshold():
    engine = SweepAbsorptionEngine(lookback=12, opposing_flow=0.20)
    assert engine.lookback == 12
    assert engine.opposing_flow == 0.20
