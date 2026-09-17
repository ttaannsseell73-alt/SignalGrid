# SignalGrid V1

SignalGrid is a deliberately small, event-driven Binance USDⓈ-M Futures engine.

## Locked V1 architecture

1. Market Data Hub — market events/state only
2. Signal Hub — VOL + STRUCTURE + FLOW -> LONG/SHORT/PASS
3. Risk Hub — permission, size and exposure caps
4. Execution Hub — bounded directional grid lifecycle only
5. State/Ops Hub — persistent positions/orders, restart recovery and health checks

No AI, ML, TradingView runtime dependency, Hummingbot runtime, Freqtrade runtime, Nautilus, Rust or multi-exchange support in V1.

## Signal core

- Volatility: NATR + short/long volatility expansion ratio
- Structure: breakout + failed breakout/liquidity sweep
- Flow: taker imbalance + order-book imbalance
- Hygiene: signal expiry/debounce
- Liquidity: spread gate

## Bounded grid execution

A valid signal maps to one finite campaign per symbol:

- 40% starter MARKET entry
- three finite pullback LIMIT entries by default
- NATR-derived spacing, bounded to safe configured limits
- one global STOP_MARKET
- one global TAKE_PROFIT_MARKET
- deterministic client IDs and campaign ownership
- no martingale, no automatic refill, no infinite grid

Restart/reconnect reconciliation is fail-closed. An active position without its owned STOP or TP is not allowed to continue silently.

## Runtime modes

### PAPER

Uses Binance production public market data with local bounded-grid execution simulation. No exchange orders are sent.

```bash
python -m signalgrid.runtime --mode paper --symbols BTCUSDT,ETHUSDT,SOLUSDT --db signalgrid.db
```

### TESTNET

Uses Binance USD-M Futures testnet REST/WebSocket endpoints and authenticated account/user-data reconciliation.

Required environment variables:

```text
BINANCE_TESTNET_API_KEY
BINANCE_TESTNET_API_SECRET
```

Run:

```bash
python -m signalgrid.runtime --mode testnet --symbols BTCUSDT,ETHUSDT,SOLUSDT --db signalgrid.db
```

TESTNET execution is blocked until account reconciliation and campaign recovery complete successfully. Every reconnect repeats recovery before the entry gate reopens.

## Offline validation

The backtest reuses the production `SignalEngine`; it does not contain a second strategy implementation.

Rules:

- signal is evaluated only after a bar closes
- entry can occur only at the next bar open
- configurable taker fees, assumed spread and slippage are charged
- optional Binance funding events are applied while a position is open
- real historical `bookTicker` is required for full-core runs; missing book data fails closed
- walk-forward selects on train data and reports only following OOS windows

Example:

```bash
python -m signalgrid.backtest.cli \
  --symbol BTCUSDT \
  --klines BTCUSDT-1m.csv \
  --bookticker BTCUSDT-bookTicker.csv \
  --funding BTCUSDT-fundingRate.csv \
  --mode walk-forward
```

## Health and soak validation

One health snapshot:

```bash
python -m signalgrid.ops.health --db signalgrid.db --pretty
```

Health fails when it detects conditions such as:

- halted runtime
- orphan account position
- unowned active order/algo order
- active position missing its campaign STOP
- active position missing its campaign TAKE_PROFIT
- TESTNET execution/user stream not ready

Append a soak sample:

```bash
python -m signalgrid.ops.soak sample --db signalgrid.db --journal soak.jsonl
```

Evaluate a 24-hour soak:

```bash
python -m signalgrid.ops.soak report --journal soak.jsonl --required-hours 24 --pretty
```

A soak does not pass unless the requested duration is observed with zero unhealthy samples, zero halts, zero protection gaps, zero orphan-state samples and zero campaign-open failures.

No live-capital mode is enabled by M6. PAPER must be clean before TESTNET validation, and TESTNET must be clean before any later live-capital work is considered.

## Tests

```bash
python -m pytest -q
```
