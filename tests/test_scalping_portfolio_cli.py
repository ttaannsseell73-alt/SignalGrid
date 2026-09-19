from signalgrid.backtest.scalping_portfolio_cli import parse_symbols


def test_portfolio_symbols_are_normalized_and_deduplicated():
    assert parse_symbols("btcusdt, ETHUSDT,btcusdt, solusdt") == (
        "BTCUSDT",
        "ETHUSDT",
        "SOLUSDT",
    )


def test_portfolio_symbols_drop_empty_values():
    assert parse_symbols("BTCUSDT,, ,ETHUSDT") == ("BTCUSDT", "ETHUSDT")
