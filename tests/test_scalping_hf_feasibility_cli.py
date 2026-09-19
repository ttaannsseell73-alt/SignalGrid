from signalgrid.backtest.scalping_hf_feasibility_cli import (
    BASE_ROUND_TRIP_COST_BPS,
    HORIZON_BARS,
    STRESS_ROUND_TRIP_COST_BPS,
    _percentile,
)


def test_feasibility_horizons_and_costs_are_explicit():
    assert HORIZON_BARS == (3, 6, 12)
    assert BASE_ROUND_TRIP_COST_BPS == 12.5
    assert STRESS_ROUND_TRIP_COST_BPS == 23.0


def test_percentile_is_deterministic():
    values = [1.0, 2.0, 3.0, 4.0]
    assert _percentile(values, 0.0) == 1.0
    assert _percentile(values, 1.0) == 4.0
    assert _percentile(values, 0.5) == 2.5
