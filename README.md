# SignalGrid — Scalping V1

Canonical branch: `scalping-v1`.

## One system

```text
Market Data Hub
  -> Coin Sonar V2 / Impulse Radar
  -> Signal Hub (VOL + STRUCTURE + FLOW)
  -> Risk
  -> bounded Execution
```

Coin Sonar is a wake layer only. It never creates LONG/SHORT trades and never bypasses Risk. Grid logic is execution only.

The single configuration source is:

```text
src/signalgrid/scalping_profile.py
```

PAPER, historical validation and Demo must use that profile.

## Run

Windows canonical entrypoint:

```text
RUN_SCALPING.cmd
```

Current stage is **PAPER only**. It uses Binance public market data and does not require API keys.

The PAPER runtime uses:
- adaptive Coin Sonar turnover/impulse detection,
- Price Action structure,
- taker-flow and live book/spread confirmation,
- fail-closed Risk,
- bounded paper execution.

Run artifacts are stored under `runs/`.

## Validation

Historical validation is fail-closed and uses the same Coin Sonar -> Signal Hub ordering. Historical archives do not provide equivalent live order-book/spread context, so those fields are not fabricated.

Historical scope:

```text
SONAR_PRICE_TAKER_NO_BOOK
```

A fresh gate must match the current profile version:

```text
SCALPING_V1_20260919_R10_WAKE60_STOP75_TTL6
```

The previous pre-Sonar results failed the profitability gate and do not unlock Demo.

## Demo

Binance Futures Demo remains locked until the current profile passes the required evidence gates. Demo credentials, when eventually needed, stay only in local `.env`; never commit them.

No live-capital mode is enabled.

## Safety invariants

- no martingale
- no infinite grid
- max 3 concurrent positions in the current risk profile
- 3x Demo leverage profile
- 3-second signal TTL
- reconciliation fail-closed
- protective stop / take-profit required
- stale/missing validation gate cannot start Demo
- Coin Sonar bypass causes CI failure

## Tests

```text
python -m pytest -q
```

GitHub Actions runs the suite on Python 3.11, 3.12 and 3.13. Dedicated smoke tests verify public market streaming and the canonical PAPER chain.

Research-only matrices and abandoned hypothesis runners are preserved on:

```text
research-archive-20260919
```

They are not part of the active scalping runtime.
