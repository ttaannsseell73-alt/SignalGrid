# PROJECT_STATE

## Canonical repository
- Repo: `ttaannsseell73-alt/SignalGrid`
- Canonical branch: `main`
- Development rule: every meaningful tested increment is committed atomically; this file is updated at every checkpoint.
- Recovery rule: after any chat/tool interruption, resume from GitHub HEAD + this file, never from conversation memory alone.

## Canonical scope
SignalGrid V1 is a small event-driven Binance USDⓈ-M Futures engine with exactly five hubs:
1. Market Data
2. Signal
3. Risk
4. Execution
5. State/Ops

## Locked constraints
- Maximum active positions: configurable; V1 default 10.
- Signal core: volatility + structure + flow + liquidity gate.
- No extra signal family enters live V1 without out-of-sample evidence replacing, not stacking on, an existing feature.
- Binance is the only exchange in V1.
- TradingView is research/optional input only, never a runtime dependency.
- No Hummingbot/Freqtrade/Passivbot runtime dependency.
- Keep transport, signal logic, risk, execution and persistence separated.
- V1 execution/account state requires Binance one-way position mode; hedge-mode state fails closed.
- New entries require an open State/Ops execution gate. Missing gate is fail-closed by default.
- Historical validation must not synthesize missing order-book data.

## Current verified baseline
- Public event parser: `aggTrade`, `bookTicker`, `kline`.
- Signal primitives: NATR, volatility expansion, breakout/failed-breakout, taker imbalance, top-of-book imbalance, spread gate.
- Risk: max positions, total notional cap, per-symbol active-position guard, signal-strength sizing.
- Scanner: event-driven, up to 50 symbols, freshness gate, debounce and compute-latency observability.
- Execution: authenticated Binance REST adapter, deterministic client IDs, symbol-filter rounding, MARKET entry, Algo STOP_MARKET protection, deterministic error classes.
- State/Ops: SQLite WAL persistence for positions, normal orders, algo orders, processed events and runtime gates.
- Recovery: authenticated user-data stream, startup/reconnect REST reconciliation, buffered gap closure, duplicate/stale event handling and fail-closed mismatch behavior.
- Offline validation: production SignalEngine replay, next-bar execution, realistic fee/spread/slippage model, funding hook, OOS walk-forward and parameter-stability report.

## Milestones
- [x] M0 project skeleton
- [x] Signal primitives
- [x] Basic risk gate
- [x] SQLite state store
- [x] Binance public payload parser
- [x] M1 live Binance public WebSocket transport
- [x] M2 multi-symbol scanner (30-50 symbols)
- [x] M3 authenticated Binance execution adapter
- [x] M4 user-data reconciliation and restart recovery
- [x] M5 backtest/walk-forward harness
- [ ] M6 paper/testnet validation

## Latest checkpoint
- M4 tracks `ORDER_TRADE_UPDATE`, `ACCOUNT_UPDATE`, `ALGO_UPDATE`, listen-key expiry and critical account-risk events.
- Open normal orders, open algo orders and one-way positions are reconciled against Binance REST on startup/reconnect.
- User events are buffered while the REST snapshot is taken, then drained before the execution gate opens.
- Duplicate/stale events are idempotent; fill regression, identity conflict, foreign order activity, hedge-mode state, margin call and reconciliation mismatch fail closed.
- New entries are blocked unless `execution_ready=True`; protective/cancel paths remain available for risk reduction.
- M5 reuses the production `SignalEngine`; there is no duplicate backtest strategy implementation.
- M5 data loaders support Binance USD-M kline, bookTicker and fundingRate CSV archive shapes and sort out-of-order historical bookTicker rows.
- Historical book snapshots are attached only at-or-before each bar close; future snapshots are never used.
- Full-core historical runs fail closed when book coverage is missing; no synthetic order book is substituted.
- Signal decisions occur at bar close and may execute only at the next bar open.
- Costs include configurable taker fees, assumed spread, slippage and funding cashflows.
- Walk-forward selects candidates on train windows and reports following OOS windows only; parameter-neighborhood stability is tracked separately from best-point performance.
- Local M5-specific verification: `9 passed` + `compileall` PASS on 2026-09-17. Repository CI remains source of truth for the full suite.

## Immediate next task
Implement M6 paper/testnet validation without adding strategy features: wire the existing scanner -> risk -> execution -> State/Ops path to Binance USD-M testnet/paper mode, record signal-to-order latency, fills/slippage, orphan/duplicate-order incidents, restart recovery, and 24h/72h soak-test health. No live-capital deployment until M6 is clean.
