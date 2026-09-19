from signalgrid.backtest.data import HistoricalBar
from signalgrid.backtest.engine import BacktestConfig, metrics_from_trades, SimulatedTrade
from signalgrid.backtest.scalping_validation import (
    ScalpingValidationGate,
    _slice_metrics,
    run_scalping_validation,
)
from signalgrid.models import Direction


def _trade(net: float, setup: str = "BREAKOUT_RETEST", regime: str = "SCALP_EXPANSION"):
    return SimulatedTrade(
        symbol="BTCUSDT",
        direction=Direction.LONG,
        setup=setup,
        signal_time_ms=1,
        entry_time_ms=2,
        exit_time_ms=3,
        entry_price=100.0,
        exit_price=101.0,
        quantity=1.0,
        gross_pnl=net + 0.1,
        fees=0.1,
        funding_cost=0.0,
        net_pnl=net,
        exit_reason="MAX_HOLD",
        holding_bars=3,
        mae_usdt=0.4,
        mfe_usdt=1.2,
        regime=regime,
    )


def test_metrics_include_mae_mfe_and_holding_time():
    m = metrics_from_trades([_trade(1.0), _trade(-0.5)], 10_000.0)
    assert m.trades == 2
    assert m.avg_holding_bars == 3.0
    assert m.avg_mae_usdt == 0.4
    assert m.avg_mfe_usdt == 1.2


def test_validation_slice_reports_setup_and_regime_dependence():
    trades = [
        _trade(1.0, "BREAKOUT_RETEST", "SCALP_EXPANSION"),
        _trade(-0.5, "BREAKOUT_RETEST", "SCALP_EXPANSION"),
        _trade(0.8, "LIQUIDITY_SWEEP_REJECTION", "SCALP_REVERSAL"),
    ]
    by_setup = _slice_metrics(trades, "setup")
    by_regime = _slice_metrics(trades, "regime")
    assert {x.key for x in by_setup} == {
        "BREAKOUT_RETEST",
        "LIQUIDITY_SWEEP_REJECTION",
    }
    assert {x.key for x in by_regime} == {"SCALP_EXPANSION", "SCALP_REVERSAL"}


def test_validation_fails_closed_when_sample_has_no_trades():
    rows = [
        HistoricalBar(
            symbol="BTCUSDT",
            open_time_ms=i * 60_000,
            close_time_ms=(i + 1) * 60_000 - 1,
            open=100.0,
            high=100.01,
            low=99.99,
            close=100.0,
            volume=1000.0,
            quote_volume=100_000.0,
            taker_buy_quote=50_000.0,
            best_bid=99.995,
            best_ask=100.005,
            bid_depth=100.0,
            ask_depth=100.0,
        )
        for i in range(70)
    ]
    report = run_scalping_validation(
        rows,
        gate=ScalpingValidationGate(min_trades=1),
    )
    assert report.passed is False
    assert report.oos_start_ms == 49 * 60_000
    assert any(reason.startswith("INSUFFICIENT_TRADES") for reason in report.reasons)
    assert any(reason.startswith("OOS_INSUFFICIENT_TRADES") for reason in report.reasons)


def test_validation_rejects_invalid_oos_fraction():
    rows = [
        HistoricalBar(
            symbol="BTCUSDT",
            open_time_ms=i * 60_000,
            close_time_ms=(i + 1) * 60_000 - 1,
            open=100.0,
            high=100.01,
            low=99.99,
            close=100.0,
            volume=1000.0,
            quote_volume=100_000.0,
            taker_buy_quote=50_000.0,
            best_bid=99.995,
            best_ask=100.005,
            bid_depth=100.0,
            ask_depth=100.0,
        )
        for i in range(70)
    ]
    import pytest
    with pytest.raises(ValueError, match="oos_fraction"):
        run_scalping_validation(
            rows,
            gate=ScalpingValidationGate(min_trades=1, oos_fraction=0.05),
        )
