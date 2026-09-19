# SignalGrid — Scalping V1

Canonical branch: `scalping-v1`.

This branch is the active Binance USDⓈ-M Futures scalping project. The old generic grid/demo strategy is not the active strategy line.

## Locked architecture

`Market Data -> Price Action -> Microstructure -> Quant Validation -> Scalping Decision -> Execution -> Risk/Reconciliation`

Primary decision logic:
- breakout + retest
- liquidity sweep / failed breakout rejection
- compression -> breakout
- spread gate
- taker-flow imbalance
- top-of-book imbalance
- volatility expansion
- short-lived signal TTL

Classic RSI/MACD/Stochastic/EMA-cross stacks are not the primary decision engine.

Grid logic is **execution only**. It is never allowed to create a trade by itself.

## Current Scalping V1 profile

- signal TTL: 3 seconds
- max spread gate: 4 bps
- structure lookback: 12 closed bars
- flow confirmation required
- no neutral-grid signal
- maximum 3 concurrent Demo positions
- 3x Demo leverage
- bounded execution: 75% starter + one pullback level
- no martingale
- no infinite refill grid
- fail-closed reconciliation

## One-click historical pipeline

For the first empirical gate on Windows:

```text
RUN_SCALPING_PIPELINE_30D.cmd
```

It performs, in order:

1. checksum-verified Binance USD-M daily 1m kline download,
2. checksum-verified historical `bookTicker` download,
3. local consolidation under `data/scalping/`,
4. no-lookahead scalping replay,
5. base-cost and stressed-cost validation,
6. gate creation only on PASS.

The 30-day download can be large because historical bookTicker is high-frequency data.

A smaller downloader is also present:

```text
RUN_SCALPING_DOWNLOAD_7D.cmd
```

Seven days is for data-pipeline smoke testing only. The canonical gate requires at least 28 days of historical span, so a 7-day sample cannot unlock Demo.

## Quant gate — mandatory before Demo

Demo is locked until historical replay passes the cost-stressed validation gate.

The validator reports:
- trades / hit rate
- net expectancy
- profit factor
- max drawdown
- fees + spread + slippage cost share
- MAE / MFE
- average holding time
- performance by setup
- performance by regime
- base friction and stressed friction results

Historical `bookTicker` coverage is mandatory. Missing order-book data is not replaced with fake proxies.

Expected local data paths:

```text
data/scalping/BTCUSDT-1m.csv
data/scalping/BTCUSDT-bookTicker.csv
data/scalping/BTCUSDT-fundingRate.csv   # optional
```

On Windows:

```text
RUN_SCALPING_VALIDATE.cmd
```

A passing validation creates the local, gitignored marker:

```text
validation/scalping_gate.json
```

The marker is tied to the current strategy profile version. A stale profile cannot unlock Demo.

## Binance Futures Demo

Only after the quant gate passes:

```text
RUN_SCALPING_DEMO.cmd
```

or:

```text
RUN_DEMO.cmd
```

First local run creates/uses `.env` for:

```text
BINANCE_DEMO_API_KEY=
BINANCE_DEMO_API_SECRET=
```

Secrets are local only and gitignored.

Demo safety:
- Binance Futures Demo endpoints only
- One-way Mode required
- preflight pins 3x leverage
- non-SignalGrid open orders fail closed
- positions outside the configured universe fail closed
- stale SignalGrid Demo state is reconciled/reset
- protective STOP/TP lifecycle remains mandatory

Default Demo universe:

`BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT, XRPUSDT`

## Direct CLI validation

```bash
python -m signalgrid.backtest.scalping_cli \
  --symbol BTCUSDT \
  --klines data/scalping/BTCUSDT-1m.csv \
  --bookticker data/scalping/BTCUSDT-bookTicker.csv \
  --funding data/scalping/BTCUSDT-fundingRate.csv \
  --gate-output validation/scalping_gate.json
```

The gate fails closed unless the supplied historical sample clears the configured minimum evidence and remains positive after stressed costs.

## Tests

```bash
python -m pytest -q
```

GitHub Actions tests Python 3.11, 3.12 and 3.13 on every push and pull request.

No live-capital mode is enabled in Scalping V1.


## Multi-symbol robustness pipeline

The preferred generalization check is now:

```text
RUN_SCALPING_PORTFOLIO_30D.cmd
```

Default symbols:

`BTCUSDT, ETHUSDT, SOLUSDT`

For each symbol the pipeline downloads 30 days of Binance USD-M 1m klines and runs the same no-lookahead scalping validation. The portfolio gate is created only if **every requested symbol** passes its own base-cost, stressed-cost, OOS and parameter-robustness checks.

The historical archive scope is intentionally `PRICE_ACTION_TAKER_ONLY`: current validation uses closed-bar price action plus the taker-buy quote field present in Binance kline archives. It does not synthesize missing historical order-book microstructure. Spread/book/absorption gates remain active in forward Demo trading.

Parameter robustness uses a 3x3 neighborhood around the locked score and expansion thresholds. At least 60% of nearby parameter variants must retain positive OOS stressed expectancy. This is intended to reject knife-edge fits rather than optimize for a single best parameter set.
