# SignalGrid V1 — GitHub Demo Branch

Canonical branch: `signalgridbot-live-v1`.

This branch is the single working line for the Binance Futures Demo bot. No ZIP-copy workflow is required after the repository is cloned once.

## Windows — one-click Demo run

1. Clone/check out branch `signalgridbot-live-v1`.
2. Double-click `RUN_DEMO.cmd`.
3. On first run, `.env` opens automatically. Enter only:
   - `BINANCE_DEMO_API_KEY`
   - `BINANCE_DEMO_API_SECRET`
4. Save the file and double-click `RUN_DEMO.cmd` again.

From that point onward, `RUN_DEMO.cmd` is enough.

The launcher:
- creates/reuses a local Python virtual environment,
- installs the current GitHub branch,
- reads credentials only from local `.env`,
- starts the Binance Futures Demo reflex bot,
- fixes leverage at 3x during Demo preflight,
- writes each run under `runs/demo-reflex-<timestamp>/`,
- keeps API credentials out of GitHub.

The Demo account must be in **One-way Mode**. Preflight fails closed on foreign/unowned open orders or positions outside the configured SignalGrid universe.

Default symbols are `BTCUSDT,ETHUSDT,SOLUSDT`. They can be changed in local `.env` with `SIGNALGRID_DEMO_SYMBOLS`.

## Architecture

1. Market Data Hub
2. Signal Hub — volatility + structure + flow
3. Risk Hub
4. Bounded grid execution
5. Persistent state / reconciliation / health

No live-capital mode is enabled. This branch is Demo validation only.

## Safety

- Demo REST: `https://demo-fapi.binance.com`
- Demo stream: `wss://demo-fstream.binance.com`
- Hedge Mode is rejected; One-way Mode is required.
- Stale SignalGrid Demo orders are reconciled/reset before a fresh run.
- Non-SignalGrid open orders fail the preflight instead of being touched.
- Positions outside the configured symbol universe fail the preflight.
- Secrets remain local in `.env`, which is gitignored.

## Tests

```bash
python -m pytest -q
```

CI runs the test suite on every push and pull request.
