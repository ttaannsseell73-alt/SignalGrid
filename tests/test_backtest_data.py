from pathlib import Path

from signalgrid.backtest.data import BookSnapshot, attach_book_snapshots, load_binance_klines_csv


def test_binance_kline_loader_keeps_book_missing(tmp_path: Path):
    p = tmp_path / "k.csv"
    p.write_text(
        "1700000000000,100,101,99,100.5,10,1700000059999,1000,5,4,400,0\n"
        "1700000060000,100.5,102,100,101.5,12,1700000119999,1200,6,6,650,0\n",
        encoding="utf-8",
    )
    rows = load_binance_klines_csv(p, "BTCUSDT")
    assert len(rows) == 2
    assert rows[0].taker_sell_quote == 600
    assert rows[0].has_book is False


def test_book_join_never_uses_future_snapshot():
    rows = [
        __import__("signalgrid.backtest.data", fromlist=["HistoricalBar"]).HistoricalBar(
            "BTCUSDT", 0, 59_999, 100, 101, 99, 100, 10, 1000, 600
        )
    ]
    future = BookSnapshot("BTCUSDT", 60_000, 99.9, 100.1, 10, 9)
    exact_past = BookSnapshot("BTCUSDT", 59_999, 99.9, 100.1, 10, 9)
    assert attach_book_snapshots(rows, [future], max_age_ms=2_000)[0].has_book is False
    joined = attach_book_snapshots(rows, [exact_past], max_age_ms=2_000)[0]
    assert joined.has_book is True
    assert joined.bid_depth == 10


def test_bookticker_and_funding_loaders_sort_real_archive_shapes(tmp_path: Path):
    from signalgrid.backtest.data import load_book_ticker_csv, load_funding_rate_csv
    book = tmp_path / "book.csv"
    book.write_text(
        "update_id,best_bid_price,best_bid_qty,best_ask_price,best_ask_qty,transaction_time,event_time\n"
        "2,100.0,4,100.2,5,2000,2000\n"
        "1,99.9,3,100.1,6,1000,1000\n",
        encoding="utf-8",
    )
    snaps = load_book_ticker_csv(book, "BTCUSDT")
    assert [x.timestamp_ms for x in snaps] == [1000, 2000]
    assert snaps[0].bid_depth == 3

    funding = tmp_path / "fund.csv"
    funding.write_text(
        "calc_time,funding_interval_hours,last_funding_rate\n"
        "2000,8,0.0002\n1000,8,-0.0001\n",
        encoding="utf-8",
    )
    points = load_funding_rate_csv(funding, "BTCUSDT")
    assert [x.timestamp_ms for x in points] == [1000, 2000]
    assert points[0].rate == -0.0001
