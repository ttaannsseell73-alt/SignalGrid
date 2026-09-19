from signalgrid.backtest.scalping_bookdepth_walkforward_cli import (
    HORIZON_BARS,
    MIN_HOLDOUT_SAMPLES,
    MIN_TRAIN_SAMPLES,
    TEST_DAYS,
    TRAIN_DAYS,
    VALIDATION_DAYS,
)


def test_bookdepth_walkforward_design_is_locked():
    assert (TRAIN_DAYS, VALIDATION_DAYS, TEST_DAYS) == (3, 2, 2)
    assert HORIZON_BARS == 20
    assert MIN_TRAIN_SAMPLES == 30
    assert MIN_HOLDOUT_SAMPLES == 15
