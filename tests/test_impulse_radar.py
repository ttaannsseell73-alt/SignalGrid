from signalgrid.market.state import SymbolState
from signalgrid.sonar.impulse_radar import (
    ImpulseRadar,
    ImpulseRadarConfig,
    ImpulseWindow,
)


def _state(symbol="BTCUSDT"):
    s = SymbolState(symbol)
    s.best_bid = 99.99
    s.best_ask = 100.01
    return s


def test_radar_is_wake_trigger_not_trade_signal():
    radar = ImpulseRadar()
    s = _state()
    assert radar.observe(s, 0) is None


def test_adaptive_turnover_impulse_emits_wake_event():
    cfg = ImpulseRadarConfig(
        windows=(ImpulseWindow(10_000, 20.0),),
        min_baseline_seconds=5,
        turnover_baseline_seconds=20,
        min_turnover_ratio=1.20,
        max_spread_bps=10.0,
        sample_interval_ms=1_000,
        wake_cooldown_ms=1_000,
    )
    radar = ImpulseRadar(cfg)
    s = _state()

    # Build a 10 USDT/s baseline.
    for second in range(0, 11):
        s.taker_buy_quote = second * 6.0
        s.taker_sell_quote = second * 4.0
        s.best_bid = 99.99
        s.best_ask = 100.01
        radar.observe(s, second * 1_000)

    # Strong price + turnover acceleration. No fixed $ turnover threshold.
    s.taker_buy_quote += 250.0
    s.taker_sell_quote += 50.0
    s.best_bid = 100.39
    s.best_ask = 100.41
    event = radar.observe(s, 11_000)

    assert event is not None
    assert event.direction == 1
    assert event.move_bps >= 20.0
    assert event.turnover_ratio >= 1.20
    assert event.impulse_count_60m == 1


def test_wide_spread_never_wakes_signal_hub():
    cfg = ImpulseRadarConfig(
        windows=(ImpulseWindow(5_000, 5.0),),
        min_baseline_seconds=2,
        turnover_baseline_seconds=5,
        min_turnover_ratio=1.0,
        max_spread_bps=5.0,
        sample_interval_ms=1_000,
    )
    radar = ImpulseRadar(cfg)
    s = _state()
    for second in range(6):
        s.taker_buy_quote = second * 100.0
        s.best_bid = 99.99
        s.best_ask = 100.01
        radar.observe(s, second * 1_000)

    s.taker_buy_quote += 500.0
    s.best_bid = 99.0
    s.best_ask = 101.0
    assert radar.observe(s, 6_000) is None
