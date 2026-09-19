from signalgrid.backtest.scalping_hf_delta_flip_cli import (
    DELTA_THRESHOLDS,
    DIRECTIONS,
    SweepDeltaFlipEngine,
)


def test_delta_flip_matrix_is_bounded_and_predeclared():
    assert DELTA_THRESHOLDS == (0.05, 0.10, 0.20)
    assert DIRECTIONS == ("BOTH", "LONG", "SHORT")
    assert len(DELTA_THRESHOLDS) * len(DIRECTIONS) == 9


def test_delta_flip_engine_rejects_invalid_threshold():
    try:
        SweepDeltaFlipEngine(0.0, "BOTH")
    except ValueError as exc:
        assert "threshold" in str(exc)
    else:
        raise AssertionError("zero threshold must fail")
