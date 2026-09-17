# PROJECT_STATE

## Canonical repository
- Repo: `ttaannsseell73-alt/SignalGrid`
- Canonical branch: `main`
- Recovery rule: after any interruption, resume from GitHub HEAD + this file, never from conversation memory alone.
- Commit rule: only tested increments are merged to `main`; feature branches may contain intermediate commits and merge by squash.

## Locked V1 scope
SignalGrid V1 has exactly five hubs:
1. Market Data
2. Signal
3. Risk
4. Execution
5. State/Ops

Locked constraints:
- Binance USDⓈ-M Futures only in V1.
- Default maximum active positions: 10.
- Signal core remains volatility + structure + flow + liquidity gate.
- No new signal family without OOS evidence replacing, not stacking on, an existing feature.
- One-way position mode only; hedge-mode state fails closed.
- New entries require an open State/Ops execution gate.
- Bounded directional grid only: default 4 entries, 40% starter MARKET, finite pullback LIMITs, NATR-bounded spacing, global STOP + global TP.
- No martingale, unbounded averaging, refill or infinite grid.
- No live-capital mode during M6.

## Verified baseline on main
- Market: `aggTrade`, `bookTicker`, `kline`, stream sharding/reconnect, 60 closed-bar startup warmup.
- Signal: NATR, volatility expansion, breakout/failed breakout, taker imbalance, order-book imbalance, spread gate.
- Risk: max positions, total notional cap, per-symbol guard, signal-strength sizing.
- Scanner: event-driven up to 50 symbols, freshness gate, debounce, latency observability.
- Execution: MARKET starter, LIMIT GTC grid entries, Algo STOP_MARKET, Algo TAKE_PROFIT_MARKET, reduce-only emergency close.
- Campaign ownership: deterministic IDs, persisted registry, restart/reconnect recovery, flat cleanup.
- State/Ops: SQLite WAL orders/algo orders/positions/events/runtime gates.
- Recovery: user-data stream + REST reconciliation + buffered gap closure + post-reconcile campaign recovery before gate reopen.
- Offline validation: production SignalEngine replay, next-bar execution, fees/spread/slippage/funding, OOS walk-forward, parameter stability.
- Runtime: explicit PAPER and Binance TESTNET modes with persisted runtime state and signal-to-order latency stats.
- Ops health: detects halt, orphan position/order/algo state, missing owned STOP/TP, TESTNET readiness and runtime failures.
- Soak telemetry: JSONL journal + 24h/72h evaluation; unhealthy sample/protection gap/orphan state/open failure invalidates the soak.
- PAPER safety: same-market-event close/re-entry suppression prevents an event from closing and immediately reopening the same campaign.

## Canonical main checkpoints
- M5 backtest/walk-forward: `0fe39210b736c788e9149af12faf3b5ecfc19366`
- M6 bounded-grid preflight: `08531441d33992e649c953fdf6c9eb6bec98087f`
- M6 Binance execution plumbing: `952abbceccab628746953b8c052c19531143a4bb`
- M6 PAPER/TESTNET runtime + reconnect-safe recovery: `c43c9638beecfc1beeb33c0b2f996201de7501e4`
- M6 soak telemetry + PAPER re-entry hardening: `a25d80bf1be5d34b5e4d82b710f99fdd03e4973d`
- Canonical state checkpoint after telemetry merge: `984086e14b8b75306d0f0edb435a33cf53f81df8`

## Milestones
- [x] M0 project skeleton
- [x] M1 public WebSocket transport
- [x] M2 30-50 symbol scanner
- [x] M3 authenticated execution adapter
- [x] M4 reconciliation/restart recovery
- [x] M5 backtest/walk-forward harness
- [x] M6 bounded-grid execution shape
- [x] M6 PAPER/TESTNET runtime wiring
- [x] M6 soak telemetry merge
- [ ] M6 one-command soak runner merge
- [ ] M6 PAPER 24h soak
- [ ] M6 TESTNET 24h soak
- [ ] M6 TESTNET 72h soak

## Active work
- Branch: `m6-one-command-soak`
- Hardened soak evaluation with a maximum allowed sample gap. A journal can no longer pass merely because its first and last healthy samples are 24h apart.
- Added `signalgrid.ops.run_soak` to start PAPER/TESTNET runtime and health sampling together, wait for LIVE state, journal automatically, fail fast on unhealthy state and emit the final report.
- Runner defaults the allowed telemetry gap to 2.5x the requested sample interval.
- Existing non-empty journals are rejected by default to prevent accidental cross-run mixing; `--overwrite-journal` is explicit.
- Added tests for continuous coverage, missing-gap failure, healthy one-command PAPER soak and fail-fast halt behavior.
- README documents one-command PAPER 24h, TESTNET 24h and TESTNET 72h usage.

## Immediate next task
1. Run full CI for `m6-one-command-soak` on Python 3.11/3.12/3.13 and fix any regression.
2. Squash-merge only if all CI jobs pass.
3. After merge, the remaining M6 work is empirical: PAPER 24h -> TESTNET 24h -> TESTNET 72h, in that order.
4. No live-capital mode until all M6 soak gates are clean and reviewed.
