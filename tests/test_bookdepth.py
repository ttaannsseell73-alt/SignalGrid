from signalgrid.backtest.bookdepth import BookDepthSnapshot, depth_quality_report


def _snap(ts, bid1, ask1):
    return BookDepthSnapshot(
        symbol="BTCUSDT",
        timestamp_ms=ts,
        bid_depth_1=bid1,
        ask_depth_1=ask1,
        bid_depth_5=bid1 * 2,
        ask_depth_5=ask1 * 2,
        bid_notional_1=bid1 * 100,
        ask_notional_1=ask1 * 100,
        bid_notional_5=bid1 * 200,
        ask_notional_5=ask1 * 200,
    )


def test_bookdepth_imbalance_sign():
    s = _snap(0, 120.0, 80.0)
    assert round(s.depth_imbalance_1, 6) == 0.2
    assert s.notional_imbalance_1 > 0


def test_bookdepth_quality_detects_frozen_side():
    snapshots = [_snap(0, 100, 50), _snap(30_000, 110, 50), _snap(60_000, 90, 50)]
    report = depth_quality_report(snapshots)
    assert report["snapshots"] == 3
    assert report["median_interval_ms"] == 30_000
    assert report["frozen_inner_ask_fraction"] == 1.0
    assert report["frozen_inner_bid_fraction"] == 0.0
