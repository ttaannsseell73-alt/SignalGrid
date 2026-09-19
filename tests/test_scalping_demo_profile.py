from signalgrid.scalping_profile import (
    DEFAULT_SCALPING_SYMBOLS,
    SCALPING_GRID_CONFIG,
    SCALPING_RISK_CONFIG,
    SCALPING_SIGNAL_CONFIG,
    SCALPING_SONAR_CONFIG,
)


def test_scalping_profile_is_short_lived_and_cost_gated():
    assert SCALPING_SIGNAL_CONFIG.ttl_seconds <= 3.0
    assert SCALPING_SIGNAL_CONFIG.max_spread_bps <= 4.0
    assert SCALPING_SIGNAL_CONFIG.structure_lookback <= 12
    assert SCALPING_SIGNAL_CONFIG.min_score >= 0.60


def test_coin_sonar_profile_is_adaptive_and_liquidity_gated():
    assert SCALPING_SONAR_CONFIG.min_turnover_ratio == 1.50
    assert SCALPING_SONAR_CONFIG.require_spread is True
    assert SCALPING_SONAR_CONFIG.turnover_mode == "CUMULATIVE_WINDOW"
    assert not hasattr(SCALPING_SONAR_CONFIG, "min_turnover_usdt")


def test_scalping_execution_is_bounded_not_infinite_grid():
    assert SCALPING_GRID_CONFIG.entry_levels == 1
    assert SCALPING_GRID_CONFIG.starter_fraction == 1.0
    assert SCALPING_GRID_CONFIG.max_spacing_bps <= 25.0
    assert SCALPING_RISK_CONFIG.max_positions == 3
    assert SCALPING_RISK_CONFIG.leverage == 3


def test_scalping_default_universe_is_historically_gated():
    assert DEFAULT_SCALPING_SYMBOLS == ("BTCUSDT",)
