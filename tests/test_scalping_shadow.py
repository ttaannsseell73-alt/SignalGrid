from signalgrid.ops.run_scalping_shadow import (
    ShadowAccumulator,
    _imbalance,
    _microprice,
    MARKET_STREAM_BASE,
    PUBLIC_STREAM_BASE,
    combined_url,
    market_stream_names,
    public_stream_names,
)


def test_shadow_routes_trade_and_book_to_split_2026_endpoints():
    public = public_stream_names(("BTCUSDT", "ETHUSDT"))
    market = market_stream_names(("BTCUSDT", "ETHUSDT"))
    assert public == [
        "btcusdt@bookTicker",
        "btcusdt@depth20@100ms",
        "ethusdt@bookTicker",
        "ethusdt@depth20@100ms",
    ]
    assert market == ["btcusdt@aggTrade", "ethusdt@aggTrade"]
    assert combined_url(PUBLIC_STREAM_BASE, public).startswith(
        "wss://fstream.binance.com/public/stream?streams="
    )
    assert combined_url(MARKET_STREAM_BASE, market).startswith(
        "wss://fstream.binance.com/market/stream?streams="
    )


def test_shadow_accumulator_emits_previous_second():
    a = ShadowAccumulator("BTCUSDT")
    assert a.on_book_ticker(
        event_ms=1_000,
        bid=99.0,
        bid_qty=2.0,
        ask=101.0,
        ask_qty=1.0,
    ) is None
    assert a.on_agg_trade(
        event_ms=1_100,
        price=100.0,
        qty=1.0,
        buyer_is_maker=False,
    ) is None
    row = a.on_depth(
        event_ms=2_000,
        bids=[["99", "2"]] * 20,
        asks=[["101", "1"]] * 20,
    )
    assert row is not None
    assert row["bucket_start_ms"] == 1_000
    assert row["buy_quote"] == 100.0
    assert row["sell_quote"] == 0.0
    assert row["trade_count"] == 1


def test_microprice_and_imbalance_direction():
    assert _imbalance(120.0, 80.0) == 0.2
    m = _microprice(99.0, 2.0, 101.0, 1.0)
    assert m is not None
    assert 99.0 < m < 101.0
