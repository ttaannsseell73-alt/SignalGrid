# PROJECT_STATE — SCALPING V1

## STATUS — ACTIVE
- Active project: Binance USDⓈ-M Futures **scalping bot**.
- Canonical repo: `ttaannsseell73-alt/SignalGrid`.
- Canonical branch: `scalping-v1`.
- Previous generic SignalGrid/grid-demo line is not the active strategy target.
- No live-capital mode is enabled.
- Binance Demo remains locked until the quant gate genuinely passes.

## LOCKED STRATEGY RULES
- Classic RSI/MACD/Stochastic/EMA-cross stacks are not the primary decision engine.
- Primary signal layer is quantified Price Action:
  - market structure / swings
  - breakout + retest
  - liquidity sweep / failed breakout
  - rejection
  - range compression -> expansion
- Microstructure:
  - live spread
  - taker-flow imbalance
  - live book imbalance
  - absorption / price-impact efficiency where source data supports it
  - OI delta only where trustworthy historical/live data exists
- Missing microstructure data is unavailable, never fabricated.
- Grid is execution only, never the strategy.
- No martingale and no infinite refill grid.
- Reconciliation is fail-closed.

## IMPLEMENTED CHECKPOINT
### Signal engine
- Dedicated `ScalpingSignalEngine`.
- 12-bar structure lookback plus quantified HH/HL vs LH/LL context.
- Breakout/retest, liquidity-sweep rejection, breakout acceptance and compression-breakout classification.
- NATR/expansion regime filter.
- Directional taker-flow confirmation.
- Live book/spread gate when live microstructure is available.
- Absorption / price-impact gate.
- Stop-distance bounds.
- 3-second signal TTL.
- No neutral grid emission.

### Quant validation
- No-lookahead replay with next-bar-open execution.
- Historical archive scope is explicitly `PRICE_ACTION_TAKER_ONLY`; unsupported historical order-book fields are not synthesized.
- Base and stressed friction scenarios.
- Net expectancy, hit rate, profit factor, drawdown and cost share.
- MAE/MFE, holding time, initial stop distance and exit-reason diagnostics.
- Setup and regime slices.
- OOS checks and local parameter robustness.
- Fail-closed `ScalpingValidationGate`.
- Passing validation writes a profile-versioned local gate marker.
- Demo runtime refuses missing, failed or stale gates.

### Execution / Demo
- Existing Binance execution, reconciliation and protective-order infrastructure reused.
- Bounded scalping execution only; no infinite refill behavior.
- Max 3 Demo positions.
- 3x Demo leverage.
- Dedicated Windows launchers remain available.
- Demo secrets remain local in `.env`.

## EMPIRICAL RESULTS — 2026-09-19
### 30-day BTC/ETH/SOL canonical profile
- Quant gate: **FAIL**.
- Validated symbols: none.
- Base and OOS stressed expectancy were negative on all three symbols.
- Parameter-neighborhood robustness: 0% positive.
- Demo correctly remained locked.

### Diagnostic finding
- Original small fixed TP was below a sensible round-trip cost margin.
- Replay and execution now support a cost-aware TP floor.
- Exit diagnostics show the dominant loss path is `INVALIDATION_STOP`; `MAX_HOLD` is also negative.
- `TAKE_PROFIT` exits are positive individually, but occur too infrequently to offset stop/timeout losses.
- Therefore fees alone are not the root cause; entry selectivity and exit geometry require improvement.

### Research completed
- 9-candidate stop-distance / score matrix: **0 passing candidates**.
- 6-candidate reward/risk × holding-time matrix: **0 passing candidates**.
- Best aggregate RR research region was 2.0R / 6 bars, but still negative after stressed costs across BTC/ETH/SOL.
- No research candidate has been promoted into the canonical strategy.

### Research in progress
- Setup × direction isolation matrix:
  - BREAKOUT_RETEST
  - LIQUIDITY_SWEEP_REJECTION
  - BREAKOUT_ACCEPTANCE
  - COMPRESSION_BREAKOUT
  - BOTH / LONG / SHORT
- Fixed research profile uses the strongest prior filter region and 2.0R / 6 bars.
- Goal: determine whether aggregate losses hide any repeatable setup/direction sub-edge.

## RESEARCH HYGIENE — LOCKED
- The current 30-day BTC/ETH/SOL window is now **exploration data** because multiple hypotheses have been inspected against it.
- No candidate discovered on this window may unlock Demo merely by passing this same window later.
- Any promoted candidate must subsequently pass a **disjoint historical validation period** not used to select that candidate, then forward Demo testing.
- Gate thresholds will not be loosened simply to force a PASS.
- A failed empirical result is retained as evidence; it is not hidden or relabeled as success.

## VALIDATION ORDER — LOCKED
1. Unit/CI tests.
2. Exploration research on the current 30-day BTC/ETH/SOL sample.
3. Freeze a candidate only if cross-symbol evidence justifies it.
4. Test the frozen candidate on a disjoint historical period.
5. Require positive base + stressed expectancy, acceptable PF/drawdown/cost share and robustness.
6. Only then unlock Binance Futures Demo.
7. Demo soak/reconciliation/failure testing.
8. Only after sufficient evidence consider very small live-capital validation.

## CURRENT GATE
**No profitable edge has been demonstrated yet. Demo remains locked.**

Current work is signal-quality research, not parameter loosening. The next promotion decision depends on the setup/direction isolation results and then a disjoint-period validation.
