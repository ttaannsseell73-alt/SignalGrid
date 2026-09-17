# SignalGrid V1

SignalGrid is a deliberately small, event-driven Binance USDⓈ-M Futures engine.

## Locked V1 architecture

1. Market Data Hub — market events/state only
2. Signal Hub — VOL + STRUCTURE + FLOW -> LONG/SHORT/PASS
3. Risk Hub — permission, size and exposure caps
4. Execution Hub — order lifecycle only
5. State/Ops Hub — persistent positions/orders and restart recovery

No AI, ML, TradingView dependency, Hummingbot runtime, Freqtrade runtime, Nautilus, Rust or multi-exchange support in V1.

## Signal core

- Volatility: NATR + short/long volatility expansion ratio
- Structure: breakout + failed breakout/liquidity sweep
- Flow: taker imbalance + order-book imbalance
- Hygiene: signal expiry/debounce
- Liquidity: spread gate

## Current milestone

`M2`: deterministic signal kernel + risk gate + SQLite state + resilient Binance public WebSocket transport + bounded multi-symbol scanner (up to 50 symbols) with freshness/debounce/latency guards.

Live public transport uses the official Binance USDⓈ-M Futures SDK, shards symbols across connections, subscribes to aggregate trades / book ticker / 1m kline, and rotates long-lived connections before the 24h boundary.

Run tests:

```bash
python -m pytest -q
```
