# PROJECT_STATE

## Canonical repository
- Repo: `ttaannsseell73-alt/SignalGrid`
- Canonical branch after review: `main`
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
- Local tests: `9 passed` on 2026-09-17 before GitHub bootstrap.
- Public event parser: `aggTrade`, `bookTicker`, `kline`.
- Signal primitives: NATR, volatility expansion, breakout/failed-breakout, taker imbalance, top-of-book imbalance, spread gate.
- Risk: max positions, total notional cap, per-symbol active-position guard, signal-strength sizing.
- State: SQLite position persistence.

## Milestones
- [x] M0 project skeleton
- [x] Signal primitives
- [x] Basic risk gate
- [x] SQLite state store
- [x] Binance public payload parser
- [x] M1 live Binance public WebSocket transport
- [ ] M2 multi-symbol scanner (30-50 symbols)
- [ ] M3 authenticated Binance execution adapter
- [ ] M4 user-data reconciliation and restart recovery
- [ ] M5 backtest/walk-forward harness
- [ ] M6 paper/testnet validation

## Latest checkpoint
- M1 live public transport implemented against Binance official USDⓈ-M Futures SDK.
- Stream sharding default: 20 symbols per connection.
- Per symbol subscriptions: aggregate trades + individual book ticker + 1m kline.
- SDK reconnect settings plus outer setup backoff and planned 23h connection rotation.
- Taker flow is bucketed by minute; late prior-bucket trades are ignored.
- Local tests: `14 passed` on 2026-09-17.

## Immediate next task
Implement M2: event-driven multi-symbol scanner/orchestrator for 30-50 symbols, with bounded evaluation frequency, signal debounce, data-freshness gate and observable signal-to-decision latency. No new signal family during M2.
