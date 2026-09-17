from signalgrid.market.binance_events import BinanceMarketEventRouter


def test_aggtrade_classifies_taker_side():
    r = BinanceMarketEventRouter()
    r.on_message({"e":"aggTrade","s":"SOLUSDT","p":"100","q":"2","m":False})
    r.on_message({"e":"aggTrade","s":"SOLUSDT","p":"101","q":"1","m":True})
    s = r.state("SOLUSDT")
    assert s.taker_buy_quote == 200
    assert s.taker_sell_quote == 101


def test_bookticker_updates_liquidity_proxy():
    r = BinanceMarketEventRouter()
    r.on_message({"e":"bookTicker","s":"SOLUSDT","b":"99.9","B":"12","a":"100.1","A":"8"})
    s = r.state("SOLUSDT")
    assert s.best_bid == 99.9
    assert s.best_ask == 100.1
    assert s.bid_depth == 12
    assert s.ask_depth == 8


def test_forming_kline_replaces_last_bar():
    r = BinanceMarketEventRouter()
    base = {"e":"kline","s":"SOLUSDT","k":{"t":1000,"o":"100","h":"101","l":"99","c":"100.5","v":"10"}}
    r.on_message(base)
    updated = {"e":"kline","s":"SOLUSDT","k":{"t":1000,"o":"100","h":"102","l":"99","c":"101.5","v":"15"}}
    r.on_message(updated)
    s = r.state("SOLUSDT")
    assert len(s.bars) == 1
    assert s.bars[-1].close == 101.5
