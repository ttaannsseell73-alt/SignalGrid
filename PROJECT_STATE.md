# PROJECT_STATE

## Canonical repository
- Repo: `ttaannsseell73-alt/SignalGrid`
- Canonical branch: `main`
- Development rule: every meaningful tested increment is committed atomically; this file is updated at every checkpoint.
- Recovery rule: after any chat/tool interruption, resume from GitHub HEAD + this file, never from conversation memory alone.

## Canonical scope
SignalGrid V1 remains a small event-driven Binance USDⓈ-M Futures engine with exactly five hubs:
1. Market Data
2. Signal
3. Risk
4. Execution
5. State/Ops

## Locked constraints
- Maximum active positions: configurable; V1 default 10.
- Signal core: volatility + structure + flow + liquidity gate.
- No extra signal family enters live V1 without OOS evidence replacing, not stacking on, an existing feature.
- Binance only in V1; no multi-exchange runtime.
- No Hummingbot/Freqtrade/Passivbot runtime dependency.
- One-way position mode only; hedge-mode state fails closed.
- New entries require an open State/Ops execution gate.
- Missing historical order-book data is never synthesized.
- Grid campaigns are bounded directional grids: one active campaign per symbol, default 4 entries, 40% starter MARKET, finite pullback LIMITs, NATR-bounded spacing, one global STOP and one global TP.
- No martingale, unbounded averaging, automatic refill or infinite grid.
- No live-capital mode until M6 paper/testnet validation is clean.

## Current verified baseline
- Public parser/transport: `aggTrade`, `bookTicker`, `kline`, stream sharding/reconnect.
- Signal: NATR + vol expansion + breakout/failed-breakout + taker/book imbalance + spread gate.
- Risk: max positions, total notional cap, per-symbol active guard, signal-strength sizing.
- Scanner: event-driven up to 50 symbols, freshness gate, debounce, latency observability.
- Execution: MARKET starter, LIMIT GTC grid entries, Algo STOP_MARKET, Algo TAKE_PROFIT_MARKET, reduce-only emergency MARKET close, deterministic client IDs and symbol-filter rounding.
- Campaign ownership: persisted grid registry, deterministic expected order IDs, cleanup and restart recovery.
- State/Ops: SQLite WAL positions/orders/algo orders/events/runtime gates.
- Recovery: user-data stream + REST reconciliation + buffered gap closure + duplicate/stale handling + fail-closed mismatch behavior.
- Offline validation: production SignalEngine replay, next-bar execution, fee/spread/slippage/funding model, OOS walk-forward and parameter stability.
- Runtime branch: PAPER/TESTNET coordinator, 60 closed-bar warmup, runtime stats, signal-to-order latency, reconnect-safe post-reconcile campaign recovery.

## Milestones
- [x] M0 project skeleton
- [x] M1 live Binance public WebSocket transport
- [x] M2 multi-symbol scanner (30-50 symbols)
- [x] M3 authenticated Binance execution adapter
- [x] M4 user-data reconciliation and restart recovery
- [x] M5 backtest/walk-forward harness
- [x] M6 bounded-grid preflight
- [x] M6 Binance bounded-grid execution plumbing
- [ ] M6 PAPER/TESTNET runtime CI + soak validation

## Latest canonical main checkpoints
- M5 merged: `0fe39210b736c788e9149af12faf3b5ecfc19366`
- Bounded-grid preflight merged: `08531441d33992e649c953fdf6c9eb6bec98087f`
- Binance bounded-grid execution plumbing merged: `952abbceccab628746953b8c052c19531143a4bb`

## Active work
- Branch: `m6-runtime-paper-testnet`
- PR: `#6` — M6 runtime: PAPER/TESTNET coordinator and reconnect-safe recovery.
- Runtime modes are explicit: PAPER uses production public market data with local execution simulation; TESTNET uses Binance USD-M testnet REST/WebSocket endpoints and authenticated State/Ops reconciliation.
- Startup warmup loads the latest 60 closed 1m bars and rejects insufficient history.
- TESTNET scanner does not evaluate entries while `execution_ready=False`.
- Every user-stream session, including reconnects, executes campaign recovery after REST/WebSocket reconciliation and before the execution gate reopens.
- A failed post-reconcile recovery halts and leaves the gate closed.
- PAPER runtime persists runtime status and stats; TESTNET runtime persists campaign/recovery/latency state.
- New runtime tests cover warmup, closed execution gate, paper campaign opening, persisted stats, post-reconcile ordering and failed recovery.

## Immediate next task
1. Run PR #6 full CI on Python 3.11/3.12/3.13 and fix any failures.
2. Add/verify same-event paper close protection and operational observability for fills/slippage/orphan/duplicate incidents.
3. Squash-merge runtime only after CI is fully green.
4. Start PAPER soak validation first, then Binance TESTNET soak validation with explicit API credentials. No live capital.
