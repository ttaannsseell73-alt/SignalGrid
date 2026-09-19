from signalgrid.backtest.highfreq import AggTrade, aggregate_aggtrades


def test_aggtrade_taker_side_and_5s_aggregation():
    trades = [
        AggTrade("BTCUSDT", 1, 100.0, 1.0, 1_000, False),
        AggTrade("BTCUSDT", 2, 101.0, 2.0, 2_000, True),
        AggTrade("BTCUSDT", 3, 102.0, 1.0, 5_500, False),
    ]
    bars = aggregate_aggtrades(trades, bucket_ms=5_000, fill_gaps=True)
    assert len(bars) == 2
    first = bars[0]
    assert first.open == 100.0
    assert first.high == 101.0
    assert first.low == 100.0
    assert first.close == 101.0
    assert first.volume == 3.0
    assert first.quote_volume == 302.0
    assert first.taker_buy_quote == 100.0
    second = bars[1]
    assert second.open == second.close == 102.0
    assert second.taker_buy_quote == 102.0


def test_aggtrade_aggregation_fills_empty_buckets():
    trades = [
        AggTrade("BTCUSDT", 1, 100.0, 1.0, 1_000, False),
        AggTrade("BTCUSDT", 2, 103.0, 1.0, 16_000, False),
    ]
    bars = aggregate_aggtrades(trades, bucket_ms=5_000, fill_gaps=True)
    assert [b.open_time_ms for b in bars] == [0, 5_000, 10_000, 15_000]
    assert bars[1].volume == 0.0
    assert bars[1].close == 100.0
    assert bars[2].close == 100.0
    assert bars[3].close == 103.0
