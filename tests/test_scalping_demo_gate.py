import json

import pytest

from signalgrid.ops.run_scalping_demo import _require_validation_gate
from signalgrid.signals.scalping import SCALPING_PROFILE_VERSION


def test_demo_gate_missing_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("SIGNALGRID_SCALPING_GATE", str(tmp_path / "missing.json"))
    with pytest.raises(RuntimeError, match="SCALPING_QUANT_GATE_MISSING"):
        _require_validation_gate()


def test_demo_gate_stale_profile_fails_closed(tmp_path, monkeypatch):
    path = tmp_path / "gate.json"
    path.write_text(
        json.dumps({"gate_passed": True, "profile_version": "OLD"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("SIGNALGRID_SCALPING_GATE", str(path))
    with pytest.raises(RuntimeError, match="SCALPING_QUANT_GATE_STALE_PROFILE"):
        _require_validation_gate()


def test_demo_gate_accepts_current_passed_profile(tmp_path, monkeypatch):
    path = tmp_path / "gate.json"
    payload = {
        "gate_passed": True,
        "profile_version": SCALPING_PROFILE_VERSION,
        "historical_microstructure_scope": "PRICE_ACTION_TAKER_ONLY",
        "symbol": "BTCUSDT",
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("SIGNALGRID_SCALPING_GATE", str(path))
    assert _require_validation_gate() == payload


def test_demo_gate_rejects_unknown_historical_scope(tmp_path, monkeypatch):
    path = tmp_path / "gate.json"
    path.write_text(
        json.dumps(
            {
                "gate_passed": True,
                "profile_version": SCALPING_PROFILE_VERSION,
                "historical_microstructure_scope": "SYNTHETIC_BOOK",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("SIGNALGRID_SCALPING_GATE", str(path))
    with pytest.raises(RuntimeError, match="SCALPING_QUANT_GATE_SCOPE_INVALID"):
        _require_validation_gate()
