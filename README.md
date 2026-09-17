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

`M5`: live market transport + multi-symbol scanner + authenticated execution + fail-closed recovery + offline no-lookahead backtest/walk-forward harness.

Execution uses deterministic client IDs, exchange symbol filters/rounding and Binance Algo Order conditional stops (`/fapi/v1/algoOrder`). New entries are safe-by-default: they require the State/Ops execution gate to be configured and open.

State/Ops persists normal orders, algo/conditional orders and account positions. On startup/reconnect the bot attaches the authenticated user-data stream, buffers events, reconciles normal orders + open algo orders + positions against Binance REST, drains buffered events, and only then enables new entries. Unknown/contradictory state, margin-call events, foreign order activity or unsupported hedge-mode state fail closed.

## M5 offline validation

The backtest reuses the production `SignalEngine`; it does not contain a second strategy implementation.

Rules:

- signal is evaluated only after a bar closes
- entry can occur only at the next bar open
- invalidation stop is checked after entry
- configurable taker fees, assumed spread and slippage are charged
- optional Binance `fundingRate` events are applied while a position is open
- full-core runs require real historical `bookTicker` snapshots; missing book data fails closed rather than being synthesized
- walk-forward selects a parameter candidate on the train window and reports only the following OOS window
- parameter-neighborhood stability is reported alongside raw performance

Example:

```bash
python -m signalgrid.backtest.cli \
  --symbol BTCUSDT \
  --klines BTCUSDT-1m.csv \
  --bookticker BTCUSDT-bookTicker.csv \
  --funding BTCUSDT-fundingRate.csv \
  --mode walk-forward
```

The command prints JSON containing OOS metrics and stability information.

M5 is intentionally a bar-close research baseline. It is not a tick-level latency simulator; live microstructure/execution behavior is validated in M6 paper/testnet.

Run tests:

```bash
python -m pytest -q
```
