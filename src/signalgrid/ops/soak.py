from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from time import time
from typing import Any, Iterable

from signalgrid.ops.health import HealthSnapshot, collect_health
from signalgrid.state.store import StateStore


@dataclass(frozen=True, slots=True)
class SoakSample:
    timestamp_ms: int
    health: dict[str, Any]


@dataclass(frozen=True, slots=True)
class SoakReport:
    passed: bool
    required_seconds: int
    observed_seconds: float
    sample_count: int
    unhealthy_samples: int
    halted_samples: int
    protection_gap_samples: int
    orphan_state_samples: int
    max_open_failures: int
    max_signal_to_order_p95_ms: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def append_sample(path: str | Path, snapshot: HealthSnapshot, timestamp_ms: int | None = None) -> SoakSample:
    sample = SoakSample(timestamp_ms or int(time() * 1000), snapshot.to_dict())
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(asdict(sample), sort_keys=True, separators=(",", ":")) + "\n")
    return sample


def load_samples(path: str | Path) -> list[SoakSample]:
    target = Path(path)
    if not target.exists():
        return []
    out: list[SoakSample] = []
    with target.open("r", encoding="utf-8") as fh:
        for line_number, line in enumerate(fh, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                raw = json.loads(text)
                out.append(SoakSample(int(raw["timestamp_ms"]), dict(raw["health"])))
            except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
                raise ValueError(f"invalid soak journal line {line_number}") from exc
    out.sort(key=lambda x: x.timestamp_ms)
    return out


def evaluate_soak(samples: Iterable[SoakSample], required_seconds: int) -> SoakReport:
    rows = sorted(samples, key=lambda x: x.timestamp_ms)
    if required_seconds <= 0:
        raise ValueError("required_seconds must be positive")
    if not rows:
        return SoakReport(False, required_seconds, 0.0, 0, 0, 0, 0, 0, 0, None)

    observed_seconds = max(0.0, (rows[-1].timestamp_ms - rows[0].timestamp_ms) / 1000.0)
    unhealthy = 0
    halted = 0
    protection_gaps = 0
    orphan_states = 0
    max_open_failures = 0
    p95_values: list[float] = []

    for sample in rows:
        health = sample.health
        if not bool(health.get("healthy", False)):
            unhealthy += 1
        if bool(health.get("halted", False)):
            halted += 1
        if health.get("missing_stops") or health.get("missing_take_profits"):
            protection_gaps += 1
        if health.get("orphan_positions") or health.get("orphan_orders") or health.get("orphan_algo_orders"):
            orphan_states += 1
        stats = health.get("runtime_stats") or {}
        try:
            max_open_failures = max(max_open_failures, int(stats.get("open_failures") or 0))
        except (TypeError, ValueError):
            unhealthy += 1
        raw_p95 = stats.get("signal_to_order_p95_ms")
        if raw_p95 is not None:
            try:
                p95_values.append(float(raw_p95))
            except (TypeError, ValueError):
                unhealthy += 1

    passed = (
        len(rows) >= 2
        and observed_seconds >= required_seconds
        and unhealthy == 0
        and halted == 0
        and protection_gaps == 0
        and orphan_states == 0
        and max_open_failures == 0
    )
    return SoakReport(
        passed=passed,
        required_seconds=required_seconds,
        observed_seconds=observed_seconds,
        sample_count=len(rows),
        unhealthy_samples=unhealthy,
        halted_samples=halted,
        protection_gap_samples=protection_gaps,
        orphan_state_samples=orphan_states,
        max_open_failures=max_open_failures,
        max_signal_to_order_p95_ms=max(p95_values) if p95_values else None,
    )


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SignalGrid soak journal")
    sub = parser.add_subparsers(dest="command", required=True)

    sample_parser = sub.add_parser("sample")
    sample_parser.add_argument("--db", default="signalgrid.db")
    sample_parser.add_argument("--journal", default="soak.jsonl")

    report_parser = sub.add_parser("report")
    report_parser.add_argument("--journal", default="soak.jsonl")
    report_parser.add_argument("--required-hours", type=float, default=24.0)
    report_parser.add_argument("--pretty", action="store_true")

    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.command == "sample":
        store = StateStore(args.db)
        try:
            sample = append_sample(args.journal, collect_health(store))
            print(json.dumps(asdict(sample), sort_keys=True))
            return 0 if sample.health.get("healthy") else 2
        finally:
            store.close()

    required_seconds = int(args.required_hours * 3600)
    report = evaluate_soak(load_samples(args.journal), required_seconds)
    print(json.dumps(report.to_dict(), indent=2 if args.pretty else None, sort_keys=True))
    return 0 if report.passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
