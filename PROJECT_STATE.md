# PROJECT_STATE — SCALPING V1

## STATUS — ACTIVE
- Active project: Binance USDⓈ-M Futures **scalping bot**.
- Canonical branch: `scalping-v1`.
- The previous generic SignalGrid/grid-demo line is not the active development target on this branch.
- Goal: fast, bounded-duration intraday scalps with deterministic execution and strict risk controls.
- First validation stages: offline replay/backtest -> Binance Futures Demo/Testnet -> only later consider small live capital.

## LOCKED STRATEGY RULES
- Classic indicator stacks such as RSI/MACD/Stochastic/EMA-cross are not the primary decision engine.
- Primary signal layer is quantified Price Action:
  - market structure / swings
  - HH/HL/LH/LL
  - range and compression
  - breakout + retest
  - liquidity sweep / failed breakout
  - rejection
  - expansion
- Microstructure layer:
  - spread
  - taker-flow imbalance / CVD where reliable
  - book imbalance
  - price-impact / absorption efficiency
  - open-interest delta only when trustworthy data is available
- Missing historical microstructure fields are marked unavailable; no fabricated proxies.
- Quant validation is mandatory:
  - hit rate
  - expectancy
  - MAE/MFE
  - holding time
  - regime dependence
  - fees + spread + slippage net result
- Grid is not the strategy. It may be used only as an execution mechanism when the scalping signal/risk layer authorizes it.

## EXECUTION TARGET
- Low-latency event-driven flow.
- Multiple simultaneous opportunities may be handled when risk limits permit.
- Entries must be immediately actionable; stale signals are discarded.
- Execution model must account for spread, slippage, fill probability and order state.
- Reconciliation is fail-closed.
- No martingale.
- No infinite grid.
- Every position has bounded risk and deterministic invalidation/exit behavior.

## ARCHITECTURE
Market Data -> Price Action Features -> Microstructure -> Regime/Structure -> Quant Score/Expectancy -> Scalping Decision -> Execution -> Risk/Reconciliation

## NEXT IMPLEMENTATION GATE
1. Preserve existing reusable Binance execution/reconciliation infrastructure.
2. Replace generic grid/demo signal profile with dedicated scalping signal engine.
3. Add scalping-specific replay tests and cost model.
4. Require statistically positive net expectancy after costs before Demo/Testnet progression.
