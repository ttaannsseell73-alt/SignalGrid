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

## Current verified baseline
- Public event parser: `aggTrade`, `bookTicker`, `kline`.
- Signal primitives: NATR, volatility expansion, breakout/failed-breakout, taker imbalance, top-of-book imbalance, spread gate.
- Risk: max positions, total notional cap, per-symbol active-position guard, signal-strength sizing.
- State: SQLite position persistence.
- Execution: authenticated Binance REST adapter boundary, deterministic client IDs, symbol-filter rounding, MARKET entry, Algo STOP_MARKET protection, deterministic error classes.

## Milestones
- [x] M0 project skeleton
- [x] Signal primitives
- [x] Basic risk gate
- [x] SQLite state store
- [x] Binance public payload parser
- [x] M1 live Binance public WebSocket transport
- [x] M2 multi-symbol scanner (30-50 symbols)
- [x] M3 authenticated Binance execution adapter
- [ ] M4 user-data reconciliation and restart recovery
- [ ] M5 backtest/walk-forward harness
- [ ] M6 paper/testnet validation

## Latest checkpoint
- M1 public transport: official Binance USDⓈ-M SDK, stream sharding, aggTrade/bookTicker/1m kline.
- M2 scanner: up to 50 symbols, per-symbol evaluation throttle, freshness gate, signal debounce, latency observability.
- M3 execution: deterministic `idempotency_key` -> client order IDs, LOT_SIZE/PRICE_FILTER/MIN_NOTIONAL enforcement, MARKET entry orders and current Binance Algo Order STOP_MARKET protection.
- Protective conditional orders use `new_algo_order`/`/fapi/v1/algoOrder`; legacy STOP_MARKET through `/fapi/v1/order` is not used.
- M3 pure execution tests passed locally on 2026-09-17; repository CI remains source of truth for full-suite verification.

## Immediate next task
Implement M4: authenticated user-data stream + REST reconciliation on startup/reconnect, persisted order/position state, duplicate/fill/cancel idempotency, and fail-closed mismatch handling. No new strategy features during M4.
