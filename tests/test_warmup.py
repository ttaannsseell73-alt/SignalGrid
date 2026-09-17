import asyncio
from time import time

from signalgrid.market.binance_events import BinanceMarketEventRouter
from signalgrid.market.warmup import BinanceKlineWarmup, WarmupConfig, WarmupError


class FakeRest:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def kline_candlestick_data(self, **kwargs):
        self.calls.append(kwargs)
        return self.rows


def _row(open_time, close_time, close):
    return [open_time, close - 0.2, close + 0.3, close - 0.4, close, 10.0, close_time]


def test_warmup_loads_only_latest_closed_bars():
    now_ms = int(time() * 1000)
    start = now_ms - 70 * 60_000
    rows = []
    for i in range(65):
        open_time = start + i * 60_000
        rows.append(_row(open_time, open_time + 59_999, 100.0 + i))
    # An explicitly still-open/current bar must never enter the warmup state.
    rows.append(_row(now_ms, now_ms + 59_999, 999.0))

    rest = FakeRest(rows)
    router = BinanceMarketEventRouter()
    warmup = BinanceKlineWarmup(rest, router, WarmupConfig(bars=60))
    result = asyncio.run(warmup.load(["solusdt"]))

    state = router.state("SOLUSDT")
    assert result == {"SOLUSDT": 60}
    assert len(state.bars) == 60
    assert state.bars[-1].close != 999.0
    assert rest.calls[0]["symbol"] == "SOLUSDT"
    assert rest.calls[0]["limit"] == 62


def test_warmup_fails_closed_when_history_is_insufficient():
    now_ms = int(time() * 1000)
    rows = []
    for i in range(20):
        open_time = now_ms - (30 - i) * 60_000
        rows.append(_row(open_time, open_time + 59_999, 100.0 + i))

    warmup = BinanceKlineWarmup(FakeRest(rows), BinanceMarketEventRouter(), WarmupConfig(bars=60))
    try:
        asyncio.run(warmup.load(["SOLUSDT"]))
    except WarmupError:
        pass
    else:
        raise AssertionError("insufficient warmup history must fail closed")
