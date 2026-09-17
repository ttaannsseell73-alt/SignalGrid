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

`M4`: live market transport + bounded multi-symbol scanner + authenticated execution + fail-closed account-state recovery.

Execution uses deterministic client IDs, exchange symbol filters/rounding and Binance Algo Order conditional stops (`/fapi/v1/algoOrder`). New entries are safe-by-default: they require the State/Ops execution gate to be configured and open.

State/Ops now persists normal orders, algo/conditional orders and account positions. On startup/reconnect the bot attaches the authenticated user-data stream, buffers events, reconciles normal orders + open algo orders + positions against Binance REST, drains buffered events, and only then enables new entries. Unknown/contradictory state, margin-call events, foreign order activity or unsupported hedge-mode state fail closed.

Run tests:

```bash
python -m pytest -q
```
