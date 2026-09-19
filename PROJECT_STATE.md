# PROJECT_STATE — SCALPING V1

## STATUS — ACTIVE
- Active project: Binance USDⓈ-M Futures **scalping bot**.
- Canonical repo: `ttaannsseell73-alt/SignalGrid`.
- Canonical branch: `scalping-v1`.
- Previous generic SignalGrid/grid-demo line is not the active strategy target.
- No live-capital mode is enabled.

## LOCKED STRATEGY RULES
- Classic RSI/MACD/Stochastic/EMA-cross stacks are not the primary decision engine.
- Primary signal layer is quantified Price Action:
  - market structure / swings
  - breakout + retest
  - liquidity sweep / failed breakout
  - rejection
  - range compression -> expansion
- Microstructure:
  - spread
  - taker-flow imbalance
  - book imbalance
  - later: absorption / price-impact efficiency
  - OI delta only where trustworthy historical/live data exists
- Missing microstructure data is unavailable, never fabricated.
- Grid is execution only, never the strategy.
- No martingale and no infinite refill grid.
- Reconciliation is fail-closed.

## IMPLEMENTED CHECKPOINT
### Signal engine
- Dedicated `ScalpingSignalEngine`.
- 12-bar structure lookback.
- Breakout/retest, liquidity-sweep rejection and compression-breakout classification.
- NATR/expansion regime filter.
- 4 bps spread gate.
- Directional taker-flow + book confirmation.
- Stop-distance bounds.
- 3-second signal TTL.
- No neutral grid emission.

### Quant validation
- Existing no-lookahead replay engine generalized for the scalping evaluator.
- Next-bar-open execution rule retained.
- Real historical `bookTicker` required.
- Base and stressed friction scenarios.
- Net expectancy, hit rate, profit factor, drawdown and cost share.
- MAE/MFE and holding-time metrics added.
- Setup and regime slices added.
- Fail-closed `ScalpingValidationGate`.
- Passing validation writes a profile-versioned local gate marker.
- Demo runtime refuses to start with missing, failed or stale quant gate.

### Execution / Demo
- Existing Binance execution, reconciliation and protective order infrastructure reused.
- Scalping execution profile: 75% starter + one bounded pullback level.
- Max 3 Demo positions.
- 3x leverage.
- Dedicated `RUN_SCALPING_DEMO.cmd` and `RUN_DEMO.cmd`.
- Dedicated `RUN_SCALPING_VALIDATE.cmd`.
- Demo secrets remain local in `.env`.

## VALIDATION ORDER — LOCKED
1. Unit/CI tests.
2. Historical full-core scalping replay with real bookTicker.
3. Base + stressed cost gate.
4. Only if gate PASS -> Binance Futures Demo.
5. Demo soak/reconciliation/failure testing.
6. Only after sufficient evidence -> consider very small live-capital validation.

## CURRENT GATE
Code/CI layer is implemented. Historical acquisition is now automated with checksum verification.

Canonical empirical action:
- `RUN_SCALPING_PIPELINE_30D.cmd`
- downloads 30 daily Binance USD-M kline + bookTicker archives,
- runs the real no-lookahead cost-stressed replay,
- requires at least 28 days of historical span,
- creates the Demo unlock marker only if the quant gate actually passes.

No claim of profitable edge is allowed until that real historical run passes. A 7-day dataset is smoke-only and cannot unlock Demo.
