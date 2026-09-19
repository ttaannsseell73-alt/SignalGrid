from signalgrid.backtest.scalping_hf_research_cli import HF_PROFILES, _bt


def test_hf_profiles_are_small_and_explicit():
    assert tuple(HF_PROFILES) == ("HF_60S", "HF_120S", "HF_180S")
    assert HF_PROFILES["HF_60S"].structure_lookback == 12
    assert HF_PROFILES["HF_120S"].structure_lookback == 24
    assert HF_PROFILES["HF_180S"].structure_lookback == 36
    assert all(cfg.require_book_microstructure is False for cfg in HF_PROFILES.values())


def test_hf_replay_uses_short_bounded_hold_and_stressed_costs():
    base = _bt(False)
    stress = _bt(True)
    assert base.max_holding_bars == 12
    assert base.tp_reward_risk_multiple == 2.0
    assert stress.taker_fee_bps > base.taker_fee_bps
    assert stress.slippage_bps > base.slippage_bps
