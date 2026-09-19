import inspect
from pathlib import Path

from signalgrid.backtest.scalping_validation import historical_scalping_engine
from signalgrid.ops.run_scalping_demo import build_scalping_demo_runtime
from signalgrid.ops.run_scalping_paper import build_scalping_paper_runtime
from signalgrid.scalping_profile import (
    SCALPING_HISTORICAL_SONAR_CONFIG,
    SCALPING_SIGNAL_CONFIG,
    SCALPING_SONAR_CONFIG,
)
from signalgrid.scanner import MultiSymbolScanner
from signalgrid.sonar.impulse_radar import ImpulseRadar, ImpulseRadarConfig


def test_locked_scalping_architecture_requires_coin_sonar_before_signal_hub():
    demo_source = inspect.getsource(build_scalping_demo_runtime)
    paper_source = inspect.getsource(build_scalping_paper_runtime)
    scanner_source = inspect.getsource(MultiSymbolScanner.on_event)

    assert "ImpulseRadar(SCALPING_SONAR_CONFIG)" in demo_source
    assert "ImpulseRadar(SCALPING_SONAR_CONFIG)" in paper_source
    assert "IMPULSE_RADAR_SLEEP" in scanner_source
    assert "self.impulse_radar.observe" in scanner_source


def test_locked_coin_sonar_is_adaptive_not_fixed_dollar_threshold():
    cfg = SCALPING_SONAR_CONFIG
    assert cfg.min_turnover_ratio > 0
    assert cfg.turnover_mode == "CUMULATIVE_WINDOW"
    assert cfg.require_spread is True
    assert not hasattr(cfg, "min_turnover_usdt")
    assert isinstance(ImpulseRadar(cfg), ImpulseRadar)


def test_project_state_declares_coin_sonar_v2_locked():
    text = Path("PROJECT_STATE.md").read_text(encoding="utf-8")
    assert "## LOCKED COIN SONAR V2" in text
    assert "Market Data Hub -> Impulse Radar wake event -> existing Signal Hub" in text


def test_historical_quant_path_is_also_sonar_gated():
    from signalgrid.sonar.gate import SonarGatedSignalEngine

    engine = historical_scalping_engine(SCALPING_SIGNAL_CONFIG)
    assert isinstance(engine, SonarGatedSignalEngine)
    assert engine.impulse_radar.config is SCALPING_HISTORICAL_SONAR_CONFIG
    assert engine.impulse_radar.config.turnover_mode == "BUCKET_TOTAL"
    assert engine.impulse_radar.config.require_spread is False


def test_single_canonical_profile_owns_signal_and_sonar_defaults():
    assert SCALPING_SIGNAL_CONFIG.ttl_seconds == 3.0
    assert SCALPING_SIGNAL_CONFIG.max_spread_bps == 4.0
    assert SCALPING_SONAR_CONFIG.windows == SCALPING_HISTORICAL_SONAR_CONFIG.windows
    assert SCALPING_SONAR_CONFIG.min_turnover_ratio == SCALPING_HISTORICAL_SONAR_CONFIG.min_turnover_ratio
