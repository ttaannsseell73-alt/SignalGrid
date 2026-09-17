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

## Current verified baseline
- Public event parser: `aggTrade`, `bookTicker`, `kline`.
- Signal primitives: NATR, volatility expansion, breakout/failed-breakout, taker imbalance, top-of-book imbalance, spread gate.
- Risk: max positions, total notional cap, per-symbol active-position guard, signal-strength sizing.
- Scanner: event-driven, up to 50 symbols, freshness gate, debounce and compute-latency observability.
- Execution: authenticated Binance REST adapter, deterministic client IDs, symbol-filter rounding, MARKET entry, Algo STOP_MARKET protection, deterministic error classes.
- State/Ops: SQLite WAL persistence for positions, normal orders, algo orders, processed events and runtime gates.
- Recovery: authenticated user-data stream, startup/reconnect REST reconciliation, buffered gap closure, duplicate/stale event handling and fail-closed mismatch behavior.

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
- [ ] M5 backtest/walk-forward harness
- [ ] M6 paper/testnet validation

## Latest checkpoint
- M3 execution uses deterministic `idempotency_key` -> client IDs, LOT_SIZE/PRICE_FILTER/MIN_NOTIONAL enforcement, MARKET entry and Binance Algo Order STOP_MARKET protection.
- M4 tracks `ORDER_TRADE_UPDATE`, `ACCOUNT_UPDATE`, `ALGO_UPDATE`, listen-key expiry and critical account-risk events.
- Open normal orders, open algo orders and one-way positions are reconciled against Binance REST on startup/reconnect.
- User events are buffered while the REST snapshot is taken, then drained before the execution gate opens.
- SDK callbacks are marshalled to the StateStore-owning asyncio loop; SQLite is never used from callback/network worker threads.
- Duplicate/stale events are idempotent; fill regression, identity conflict, foreign order activity, hedge-mode state, margin call and reconciliation mismatch fail closed.
- New entries are blocked unless `execution_ready=True`; protective/cancel paths remain available for risk reduction.
- Local full suite: `44 passed` on 2026-09-17.

## Immediate next task
Implement M5 as a deliberately small offline backtest/walk-forward harness for the existing V1 signal core only. Include realistic fees, spread/slippage, funding hook, no-lookahead event ordering, per-symbol/OOS metrics and parameter-stability checks. Do not add new signal families during M5.
