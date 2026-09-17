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
- Soak coverage: maximum sample-gap validation prevents sparse journals from faking a continuous 24h/72h run.
- One-command soak runner: runtime startup + LIVE wait + health sampling + fail-fast + final report in one process.
- PAPER safety: same-market-event close/re-entry suppression prevents an event from closing and immediately reopening the same campaign.
- Binance Futures Demo reflex path: secure local Demo credentials, Demo REST/stream endpoints, One-way Mode preflight, 3x leverage pinning, reduced Demo-only risk profile and PASS/LONG/SHORT transition telemetry.

## Canonical main checkpoints
- M5 backtest/walk-forward: `0fe39210b736c788e9149af12faf3b5ecfc19366`
- M6 bounded-grid preflight: `08531441d33992e649c953fdf6c9eb6bec98087f`
- M6 Binance execution plumbing: `952abbceccab628746953b8c052c19531143a4bb`
- M6 PAPER/TESTNET runtime + reconnect-safe recovery: `c43c9638beecfc1beeb33c0b2f996201de7501e4`
- M6 soak telemetry + PAPER re-entry hardening: `a25d80bf1be5d34b5e4d82b710f99fdd03e4973d`
- M6 one-command soak runner + coverage hardening: `58e3669e1c2585bf4a390a74b9d855fd4c20539b`
- M6 Binance Futures Demo reflex runner: `765cf313e3575d5af1474338a58c9f11e192c62a`

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
- [x] M6 one-command soak runner merge
- [x] M6 Binance Futures Demo reflex runner
- [ ] M6 Binance Demo 1h reflex smoke/stress
- [ ] M6 PAPER 24h soak
- [ ] M6 TESTNET/DEMO 24h soak
- [ ] M6 TESTNET/DEMO 72h soak

## Latest verification
- PR #9 merged by squash as `765cf313e3575d5af1474338a58c9f11e192c62a`.
- PR #9 CI: Python 3.11 PASS, 3.12 PASS, 3.13 PASS.
- Demo runner never uses production credentials; launcher reads Demo secret locally and removes credential environment variables at exit.
- Demo preflight rejects Hedge Mode and pins every stress-test symbol to 3x leverage before runtime starts.
- Demo-only risk profile: max 6 positions, 1,200 USDT total notional, 100 USDT base notional, 200 USDT max per campaign, 3x leverage.
- Reflex telemetry records direction transitions, LONG↔SHORT reversals, trade→PASS invalidations, PASS→trade activations and transition-response latency.
- No live-capital path is enabled.

## Immediate next task
1. Create a Binance Futures Demo API key from the Demo Trading account API Management page; never paste its secret into chat.
2. Pull canonical `main` and run `scripts/start_demo_reflex.ps1`.
3. First empirical gate: 1-hour Demo reflex smoke/stress using BTC/ETH controls plus a liquid/high-volatility stress basket.
4. Review visible Binance Demo positions/orders together with local reflex/runtime telemetry.
5. Only after the 1-hour gate is clean, extend to 24h and then 72h empirical validation.
