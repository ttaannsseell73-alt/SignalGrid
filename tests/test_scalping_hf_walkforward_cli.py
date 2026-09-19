from signalgrid.backtest.scalping_hf_walkforward_cli import (
    CUM_FLOW_BARS,
    HORIZON_BARS,
    MIN_HOLDOUT_SAMPLES,
    MIN_TRAIN_SAMPLES,
    MOMENTUM_BARS,
    TEST_DAYS,
    TRAIN_DAYS,
    VALIDATION_DAYS,
)


def test_walkforward_research_shape_is_locked():
    assert (TRAIN_DAYS, VALIDATION_DAYS, TEST_DAYS) == (3, 2, 2)
    assert HORIZON_BARS == 60
    assert CUM_FLOW_BARS == 6
    assert MOMENTUM_BARS == 12
    assert MIN_TRAIN_SAMPLES == 50
    assert MIN_HOLDOUT_SAMPLES == 20
