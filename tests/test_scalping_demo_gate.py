import json

import pytest

from signalgrid.ops.run_scalping_demo import _require_validated_symbols, _require_validation_gate
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
        "historical_microstructure_scope": "SONAR_PRICE_TAKER_NO_BOOK",
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


def test_demo_symbol_gate_blocks_unvalidated_symbols():
    payload = {"validated_symbols": ["BTCUSDT"]}
    _require_validated_symbols(payload, ("BTCUSDT",))
    with pytest.raises(RuntimeError, match="SCALPING_UNVALIDATED_SYMBOLS:ETHUSDT"):
        _require_validated_symbols(payload, ("BTCUSDT", "ETHUSDT"))
