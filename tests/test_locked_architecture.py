import inspect
from pathlib import Path

from signalgrid.ops.run_scalping_demo import build_scalping_demo_runtime
from signalgrid.scanner import MultiSymbolScanner
from signalgrid.sonar.impulse_radar import ImpulseRadar, ImpulseRadarConfig


def test_locked_scalping_architecture_requires_coin_sonar_before_signal_hub():
    demo_source = inspect.getsource(build_scalping_demo_runtime)
    scanner_source = inspect.getsource(MultiSymbolScanner.on_event)

    assert "impulse_radar=ImpulseRadar()" in demo_source
    assert "IMPULSE_RADAR_SLEEP" in scanner_source
    assert "self.impulse_radar.observe" in scanner_source


def test_locked_coin_sonar_is_adaptive_not_fixed_dollar_threshold():
    cfg = ImpulseRadarConfig()
    assert cfg.min_turnover_ratio > 0
    assert not hasattr(cfg, "min_turnover_usdt")
    assert isinstance(ImpulseRadar(cfg), ImpulseRadar)


def test_project_state_declares_coin_sonar_v2_locked():
    text = Path("PROJECT_STATE.md").read_text(encoding="utf-8")
    assert "## LOCKED COIN SONAR V2" in text
    assert "Market Data Hub -> Impulse Radar wake event -> existing Signal Hub" in text
