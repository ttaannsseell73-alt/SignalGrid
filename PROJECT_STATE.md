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

## Canonical main checkpoints
- M5 backtest/walk-forward: `0fe39210b736c788e9149af12faf3b5ecfc19366`
- M6 bounded-grid preflight: `08531441d33992e649c953fdf6c9eb6bec98087f`
- M6 Binance execution plumbing: `952abbceccab628746953b8c052c19531143a4bb`
- M6 PAPER/TESTNET runtime + reconnect-safe recovery: `c43c9638beecfc1beeb33c0b2f996201de7501e4`

## Milestones
- [x] M0 project skeleton
- [x] M1 public WebSocket transport
- [x] M2 30-50 symbol scanner
- [x] M3 authenticated execution adapter
- [x] M4 reconciliation/restart recovery
- [x] M5 backtest/walk-forward harness
- [x] M6 bounded-grid execution shape
- [x] M6 PAPER/TESTNET runtime wiring
- [ ] M6 soak telemetry merge
- [ ] M6 PAPER 24h soak
- [ ] M6 TESTNET 24h soak
- [ ] M6 TESTNET 72h soak

## Active work
- Branch: `m6-soak-telemetry`
- Added `signalgrid.ops.health`:
  - detects halted runtime
  - orphan account positions
  - unowned active normal/algo orders
  - active positions missing owned STOP or TAKE_PROFIT
  - TESTNET gate/user-stream readiness
  - exposes runtime latency/open-failure stats
- Added `signalgrid.ops.soak`:
  - JSONL health journal
  - 24h/72h duration evaluation
  - fails on any unhealthy sample, halt, protection gap, orphan state or campaign-open failure
  - reports maximum observed signal-to-order p95 latency
- Added same-market-event PAPER re-entry suppression so one event cannot close and immediately reopen the same campaign.
- README includes PAPER, TESTNET, health and soak commands.

## Immediate next task
1. Run full CI for `m6-soak-telemetry` on Python 3.11/3.12/3.13 and fix any regression.
2. Squash-merge only if all CI jobs pass.
3. Begin real PAPER soak on a continuously running machine and journal health samples.
4. After clean PAPER validation, run Binance TESTNET 24h then 72h soak with testnet API credentials.
5. Do not enable live capital in M6.
