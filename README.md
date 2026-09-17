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

`M3`: live market transport + bounded multi-symbol scanner + authenticated Binance execution adapter.

Execution uses deterministic client IDs, exchange symbol filters/rounding and current Binance Algo Order conditional stops (`/fapi/v1/algoOrder`) rather than legacy STOP_MARKET through the normal order endpoint.

Run tests:

```bash
python -m pytest -q
```
