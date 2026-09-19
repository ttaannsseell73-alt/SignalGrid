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
- M6 Demo signal-active reflex profile + diagnostics: `68cecc367291441cd4861f4ec661a5fce7f3090a`
- M6 cleanup idempotency hardening for Binance -2011 races: `32725fa41479374b96ead84900ca3ddb57622988`
- M6 pre-exposure starter failure hardening + root-cause telemetry: `e80243ba78f209080596c381b04dcaa1dfe2a2eb`
- M6 protective trigger basis/geometry hardening: `6770b55b7f7a854467e433e79862f1a82968a687`
- M6 Demo stale-owned-order preflight cleanup: `e2253c06aef76d79e90984ca39cdeb686452af8e`
- M6 Demo stale-position reset hardening: `5c1e777801c790d3cfa4bef9a344b919de0d0826`
- M6 Demo/runtime REST timeout hardening: `81003302410f0d5627003f7ecd1d2856ab64fd3b`
- M7 bounded neutral-grid regime + execution: `6d2b817dde56a3efef722a3226c7bd5507f51d8e`

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
- First authenticated Demo run proved connectivity/execution readiness but produced 0 signals after 42,442 market events and 15,698 evaluations; open failures remained 0. Root cause is signal gating, not API connectivity.
- PR #10 merged by squash as `68cecc367291441cd4861f4ec661a5fce7f3090a`; CI PASS on Python 3.11/3.12/3.13.
- Demo reflex now uses a Demo-only reactive signal profile: structure lookback 5, max spread 10 bps, min expansion 0.90, strong expansion 1.20, min flow abs 0.05, min book abs 0.03, entry threshold 0.52. Production SignalConfig defaults are unchanged.
- Demo telemetry now exposes PASS rejection reasons, LONG/SHORT signals seen, emitted LONG/SHORT counts, and 10-second console progress.
- Authenticated Demo rerun on 2026-09-18 proved directional signal/execution path: 1 emitted LONG, 1 campaign opened, 0 open failures; rejection distribution at failure point included NO_STRUCTURE 48, NO_VOL_EXPANSION 29, LIQUIDITY_GATE 15, SYMBOL_ALREADY_ACTIVE 15.
- The same run exposed a cleanup race: Binance returned -2011 `Unknown order sent` when a flat-campaign sibling algo order had already disappeared from the exchange. This incorrectly halted the soak.
- PR #11 merged as `32725fa41479374b96ead84900ca3ddb57622988`; CI PASS on Python 3.11/3.12/3.13. Flat cleanup now treats only Binance order-not-found (-2011) as pending exchange/user-stream convergence; other cleanup failures remain fail-closed.
- Next authenticated Demo run produced 44 LONG signals seen, 2 emitted LONG signals, 1 campaign opened, then `GRID_OPEN_DEGRADED:BTCUSDT`. Health at failure showed only one full campaign footprint (1 position, 3 active regular orders, 2 active algos) despite 2 active campaign records, strongly indicating the second BTC campaign failed at starter entry before exposure.
- PR #12 merged as `e80243ba78f209080596c381b04dcaa1dfe2a2eb`; CI PASS on Python 3.11/3.12/3.13. Starter failures before any exposure now mark the campaign FAILED without account HALT; once exposure exists, stop/protection failures remain fail-closed. Runtime now persists `last_open_error` with phase, symbol, campaign, exception and root cause; Demo progress surfaces it directly.
- Authenticated Demo then opened 4 campaigns successfully before APTUSDT failed at protective STOP placement with Binance `-2021 Order would immediately trigger`; emergency close succeeded and fail-closed account HALT behaved as designed. Root cause: signal invalidation comes from regular futures kline/contract-price structure, while protective orders were configured with `MARK_PRICE` trigger basis.
- PR #13 merged as `6770b55b7f7a854467e433e79862f1a82968a687`; CI PASS on Python 3.11/3.12/3.13. Protective STOP/TP now use `CONTRACT_PRICE`, matching the signal/invalidation basis. A 2 bps trigger-geometry guard validates STOP and TP against current contract price before starter exposure, then validates again at protective submit time. Invalid geometry fails before exposure without account HALT; any post-exposure protection failure remains fail-closed with emergency close.
- Fresh Demo rerun then failed at startup reconciliation because 8 stale SignalGrid LIMIT orders (`sg-g-*`) from prior runs remained on Binance Demo while the new run used a fresh SQLite DB. This is expected fail-closed behavior, but required manual cleanup.
- PR #14 merged as `e2253c06aef76d79e90984ca39cdeb686452af8e`; CI PASS on Python 3.11/3.12/3.13. Demo preflight now refuses any open position, refuses foreign/non-SignalGrid open orders, automatically cancels only stale `sg-*` regular/algo orders, verifies the account is empty, then pins leverage and starts reconciliation. This removed manual stale-order cleanup, but the next run still found stale FETUSDT/NEARUSDT positions from prior tests and stopped safely.
- PR #15 merged as `5c1e777801c790d3cfa4bef9a344b919de0d0826`; CI PASS on Python 3.11/3.12/3.13. Demo-only preflight now resets stale SignalGrid test exposure automatically: it refuses foreign/non-SignalGrid open orders or positions outside the configured stress universe, cancels owned entry orders first, closes remaining configured Demo positions with reduce-only MARKET orders while protective algos remain active, waits until flat, cancels remaining owned algos, verifies the account is fully clean, then pins leverage and starts reconciliation. Production/live paths are unchanged.
- Next Demo start failed while setting leverage because Binance SDK REST default timeout is 1000 ms and `demo-fapi.binance.com` exceeded that read timeout. PR #16 merged as `81003302410f0d5627003f7ecd1d2856ab64fd3b`; CI PASS on Python 3.11/3.12/3.13. SignalGrid REST timeout is now 5000 ms with explicit 3 retries / 500 ms backoff across runtime REST clients. Demo preflight additionally retries only idempotent calls (position-mode read and leverage setter) on transient network errors; state-changing reset MARKET orders are not blindly retried.
- Authenticated Demo run on 2026-09-19 achieved the first clean directional lifecycle: 1 campaign opened, 1 campaign closed, 1 cleanup completed, 0 open failures, `last_open_error=null`; no recurrence of prior reconciliation, -2011, -2021, or 1-second timeout failures.
- PR #17 merged as `6d2b817dde56a3efef722a3226c7bd5507f51d8e`; CI PASS on Python 3.11/3.12/3.13 (112 tests). The locked four-outcome regime model is now implemented: `LONG_GRID / SHORT_GRID / NEUTRAL_GRID / PASS`.
- `NEUTRAL_GRID` uses only the existing volatility + structure + flow family: balanced/compressed RANGE states can arm a finite symmetric LIMIT ladder with no MARKET starter. First fill determines LONG or SHORT, installs global STOP/TP immediately, cancels opposing entries, retains only same-side bounded ladder entries, and stays fail-closed on protection/direction ambiguity. PAPER and Demo telemetry support the neutral path.
- Dynamic leverage remains the next required Risk Hub feature after one authenticated Demo neutral lifecycle is proven clean.
- No live-capital path is enabled.

## Immediate next task
1. Pull canonical `main` containing M7 neutral-grid execution and rerun `scripts/start_demo_reflex.ps1` with the Binance Futures Demo credentials entered locally.
2. Watch new neutral telemetry: `neutral_signals_seen`, `emitted_neutral`, `mode_transitions`, plus ordinary runtime counters.
3. Validation gate: observe at least one `NEUTRAL_GRID` arm and, ideally, one first-fill activation that becomes protected LONG/SHORT without `open_failures`, HALT, orphan state, or protection gaps.
4. After one clean authenticated neutral lifecycle, implement the Dynamic Leverage Controller inside Risk Hub; do not add a sixth hub.
5. Then rerun the 1h Demo stress gate and proceed to 24h and 72h Demo validation.
