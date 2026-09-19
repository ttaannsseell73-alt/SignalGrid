from signalgrid.backtest.scalping_hypothesis_cli import (
    SCORE_CANDIDATES,
    STOP_CANDIDATES,
    _parse_symbols,
)


def test_research_matrix_is_small_and_predeclared():
    assert STOP_CANDIDATES == (12.0, 18.0, 24.0)
    assert SCORE_CANDIDATES == (0.62, 0.70, 0.78)
    assert len(STOP_CANDIDATES) * len(SCORE_CANDIDATES) == 9


def test_research_symbol_parser_normalizes_and_deduplicates():
    assert _parse_symbols("btcusdt,ETHUSDT,btcusdt") == ("BTCUSDT", "ETHUSDT")
