# PROJECT_STATE — SCALPING V1

## CANONICAL STATUS
- Repo: `ttaannsseell73-alt/SignalGrid`
- Branch: `scalping-v1`
- Active system: Binance USDⓈ-M Futures scalping bot.
- Canonical entrypoint: `RUN_SCALPING.cmd`
- Current execution stage: **forward PAPER only**.
- Binance Demo and live capital remain locked.
- Research-only experiments were removed from the active branch and preserved in `research-archive-20260919`.

## LOCKED COIN SONAR V2 / ARCHITECTURE
`Market Data Hub -> Coin Sonar V2 / Impulse Radar -> existing Signal Hub -> VOL + STRUCTURE + FLOW -> Risk -> bounded Execution`

### Coin Sonar V2
- Wake/event layer only; never creates LONG/SHORT by itself.
- Adaptive per-symbol turnover baseline; no fixed dollar-volume trigger.
- Uses short-horizon price impulse, quote turnover, taker imbalance, spread/liquidity and persistence/acceleration.
- Reference impulse tiers remain:
  - <=1m: ~0.5%
  - 3–5m: ~1.5%
  - 5–10m: ~1.8%
  - ~15m: ~2.0%
- If Sonar does not wake a symbol, Signal Hub is not evaluated.

### Signal Hub
- Quantified Price Action:
  - breakout/retest
  - liquidity sweep / failed breakout
  - rejection
  - compression -> expansion
  - HH/HL and LH/LL context
- Flow/microstructure confirmation:
  - taker-flow imbalance
  - live book imbalance
  - spread gate
  - absorption / price-impact efficiency
- Classic RSI/MACD/Stochastic/EMA-cross stacks are not the primary engine.
- Signal TTL: 3 seconds.
- No neutral-grid signal.

### Risk / Execution
- Grid is execution only, never the strategy.
- Single bounded entry in current profile.
- Max 3 concurrent positions.
- 3x leverage profile for Demo stage.
- No martingale.
- No infinite refill grid.
- Reconciliation is fail-closed.
- Protective stop / take-profit lifecycle remains mandatory.

## SINGLE CANONICAL PROFILE
`src/signalgrid/scalping_profile.py` owns the shared configuration for:
- Coin Sonar V2
- Signal Hub
- Risk
- Execution
- historical Sonar projection

PAPER, historical validation and Demo must derive from this profile. CI fails if Coin Sonar is bypassed.

Profile version:
`SCALPING_V1_20260919_R5_STOP_75`

## VERIFIED CHECKPOINTS
- Python unit/CI suite passes on Python 3.11 / 3.12 / 3.13 at the last clean checkpoint.
- Binance production WebSocket routing is split correctly:
  - `/public`: bookTicker / public book data
  - `/market`: aggTrade / kline
- Runtime public-stream smoke has passed with live market events.
- Canonical PAPER chain has completed a clean forward smoke:
  - real Binance archived 1m warmup data
  - live Binance public WebSocket events
  - Coin Sonar V2 enabled
  - Paper-only execution
  - no API keys
  - no open failures / halt / orphan-state / protection-gap failures
- GitHub runner cannot use Binance production REST warmup because of runner location restrictions; CI uses checksum-verified Binance public archive warmup instead. Local PAPER keeps normal REST warmup by default.

## HISTORICAL QUANT GATE
Historical validation follows the same locked ordering:
`Coin Sonar historical projection -> Signal Hub -> cost-stressed replay`

Historical scope:
`SONAR_PRICE_TAKER_NO_BOOK`

Historical book/spread fields are not fabricated. Historical Sonar uses real kline price + quote turnover/taker-flow with `BUCKET_TOTAL`; live spread/book gates remain forward-only evidence.

The previous pre-Sonar empirical profile failed positive-expectancy gates. It is not evidence of profitability and cannot unlock Demo. The R5 stop-bounded Sonar-locked profile requires a fresh validation artifact.

## PROMOTION ORDER
1. CI + runtime/public-stream smoke PASS.
2. Canonical forward PAPER chain PASS and accumulate sufficient telemetry.
3. Fresh R5 historical cost-stressed validation PASS.
4. Only then consider Binance Futures Demo.
5. Demo soak/reconciliation/failure testing.
6. Live capital remains out of scope until evidence supports it.

## CURRENT GATE
**No profitable edge has been demonstrated. Demo remains locked.**

Active work is now one system only. No new research matrix/workflow is added to `scalping-v1`; experiments, if ever reopened explicitly, belong on the archive/research branch.
