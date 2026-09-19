from __future__ import annotations

from signalgrid.execution.grid import GridConfig
from signalgrid.risk.engine import RiskConfig
from signalgrid.signals.scalping import ScalpingConfig
from signalgrid.sonar.impulse_radar import ImpulseRadarConfig


DEFAULT_SCALPING_SYMBOLS = ("BTCUSDT",)

# Single canonical profile shared by PAPER, historical validation and Demo.
SCALPING_SIGNAL_CONFIG = ScalpingConfig(
    structure_lookback=12,
    compression_lookback=8,
    compression_baseline=30,
    compression_ratio_max=0.78,
    retest_tolerance_bps=8.0,
    max_spread_bps=4.0,
    min_natr_bps=4.0,
    max_natr_bps=180.0,
    min_expansion=0.90,
    strong_expansion=1.35,
    min_directional_flow=0.03,
    min_score=0.62,
    min_stop_bps=3.0,
    max_stop_bps=75.0,
    ttl_seconds=3.0,
)

SCALPING_SONAR_CONFIG = ImpulseRadarConfig(
    min_baseline_seconds=60,
    turnover_baseline_seconds=300,
    min_turnover_ratio=1.50,
    max_spread_bps=8.0,
    sample_interval_ms=1_000,
    persistence_window_ms=3_600_000,
    wake_cooldown_ms=2_000,
    turnover_mode="CUMULATIVE_WINDOW",
    require_spread=True,
)

SCALPING_HISTORICAL_SONAR_CONFIG = ImpulseRadarConfig(
    windows=SCALPING_SONAR_CONFIG.windows,
    min_baseline_seconds=SCALPING_SONAR_CONFIG.min_baseline_seconds,
    turnover_baseline_seconds=SCALPING_SONAR_CONFIG.turnover_baseline_seconds,
    min_turnover_ratio=SCALPING_SONAR_CONFIG.min_turnover_ratio,
    max_spread_bps=SCALPING_SONAR_CONFIG.max_spread_bps,
    sample_interval_ms=60_000,
    persistence_window_ms=SCALPING_SONAR_CONFIG.persistence_window_ms,
    wake_cooldown_ms=SCALPING_SONAR_CONFIG.wake_cooldown_ms,
    turnover_mode="BUCKET_TOTAL",
    require_spread=False,
)

SCALPING_RISK_CONFIG = RiskConfig(
    max_positions=3,
    max_total_notional_usdt=360.0,
    base_notional_usdt=60.0,
    max_notional_per_trade_usdt=120.0,
    leverage=3,
)

SCALPING_GRID_CONFIG = GridConfig(
    entry_levels=1,
    starter_fraction=1.0,
    spacing_natr_multiplier=0.20,
    min_spacing_bps=4.0,
    max_spacing_bps=25.0,
    take_profit_steps=1.2,
    min_take_profit_bps=30.0,
)
