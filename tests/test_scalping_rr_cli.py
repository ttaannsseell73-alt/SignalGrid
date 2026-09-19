from signalgrid.backtest.scalping_rr_cli import (
    HOLD_CANDIDATES,
    RR_CANDIDATES,
    _bt_config,
)


def test_rr_matrix_is_small_and_predeclared():
    assert RR_CANDIDATES == (1.5, 2.0, 2.5)
    assert HOLD_CANDIDATES == (6, 12)
    assert len(RR_CANDIDATES) * len(HOLD_CANDIDATES) == 6


def test_rr_matrix_uses_entry_risk_target():
    cfg = _bt_config(12, 2.0, True)
    assert cfg.tp_reward_risk_multiple == 2.0
    assert cfg.max_holding_bars == 12
    assert cfg.taker_fee_bps == 7.0
