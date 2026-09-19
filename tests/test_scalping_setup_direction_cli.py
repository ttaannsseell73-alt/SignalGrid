from signalgrid.backtest.scalping_setup_direction_cli import (
    DIRECTION_CANDIDATES,
    SETUP_CANDIDATES,
)


def test_setup_direction_matrix_is_predeclared_and_bounded():
    assert SETUP_CANDIDATES == (
        "BREAKOUT_RETEST",
        "LIQUIDITY_SWEEP_REJECTION",
        "BREAKOUT_ACCEPTANCE",
        "COMPRESSION_BREAKOUT",
    )
    assert DIRECTION_CANDIDATES == ("BOTH", "LONG", "SHORT")
    assert len(SETUP_CANDIDATES) * len(DIRECTION_CANDIDATES) == 12
