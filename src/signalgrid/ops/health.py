from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from signalgrid.execution.campaign import ACTIVE_CAMPAIGN_STATUSES, GridCampaignRegistry
from signalgrid.state.store import ACTIVE_ALGO_STATUSES, StateStore


@dataclass(frozen=True, slots=True)
class HealthSnapshot:
    healthy: bool
    runtime_mode: str
    runtime_status: str
    execution_ready: bool
    user_stream_status: str
    halted: bool
    halt_reason: str | None
    account_positions: int
    active_campaigns: int
    active_orders: int
    active_algo_orders: int
    orphan_positions: tuple[str, ...]
    orphan_orders: tuple[str, ...]
    orphan_algo_orders: tuple[str, ...]
    missing_stops: tuple[str, ...]
    missing_take_profits: tuple[str, ...]
    runtime_stats: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _runtime_stats(store: StateStore) -> dict[str, Any]:
    raw = store.get_runtime("runtime_stats", "{}") or "{}"
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"parse_error": True, "raw": raw}
    return parsed if isinstance(parsed, dict) else {"parse_error": True, "raw": raw}


def collect_health(store: StateStore) -> HealthSnapshot:
    registry = GridCampaignRegistry(store)
    records = tuple(r for r in registry.records() if r.status in ACTIVE_CAMPAIGN_STATUSES)
    positions = tuple(store.list_account_positions())
    active_orders = tuple(store.list_orders(active_only=True))
    active_algos = tuple(store.list_algo_orders(active_only=True))

    campaign_symbols = {r.symbol for r in records}
    expected_orders = {
        client_id
        for r in records
        for client_id in (r.starter_client_id, *r.limit_client_ids)
        if client_id
    }
    expected_algos = {
        client_id
        for r in records
        for client_id in (r.stop_client_id, r.take_profit_client_id)
        if client_id
    }

    orphan_positions = tuple(sorted(p.symbol for p in positions if p.symbol not in campaign_symbols))
    orphan_orders = tuple(
        sorted(
            o.client_order_id
            for o in active_orders
            if o.client_order_id and o.client_order_id not in expected_orders
        )
    )
    orphan_algos = tuple(
        sorted(
            a.client_algo_id
            for a in active_algos
            if a.client_algo_id and a.client_algo_id not in expected_algos
        )
    )

    active_algo_by_id = {
        a.client_algo_id: a
        for a in active_algos
        if a.client_algo_id and a.status in ACTIVE_ALGO_STATUSES
    }
    positioned_symbols = {p.symbol for p in positions if p.quantity != 0}
    missing_stops: list[str] = []
    missing_tps: list[str] = []
    for r in records:
        if r.symbol not in positioned_symbols:
            continue
        if r.stop_client_id not in active_algo_by_id:
            missing_stops.append(r.symbol)
        if r.take_profit_client_id not in active_algo_by_id:
            missing_tps.append(r.symbol)

    mode = store.get_runtime("runtime_mode", "UNKNOWN") or "UNKNOWN"
    status = store.get_runtime("runtime_status", "UNKNOWN") or "UNKNOWN"
    user_status = store.get_runtime("user_stream_status", "UNKNOWN") or "UNKNOWN"
    halted = store.halted()
    execution_ready = store.execution_ready()
    stats = _runtime_stats(store)

    connectivity_ok = True
    if mode == "TESTNET":
        connectivity_ok = status == "TESTNET_LIVE" and user_status == "LIVE" and execution_ready
    elif mode == "PAPER":
        connectivity_ok = status in {"PAPER_LIVE", "STOPPED"}

    critical_state = bool(
        halted
        or orphan_positions
        or orphan_orders
        or orphan_algos
        or missing_stops
        or missing_tps
        or stats.get("parse_error")
    )
    healthy = connectivity_ok and not critical_state

    return HealthSnapshot(
        healthy=healthy,
        runtime_mode=mode,
        runtime_status=status,
        execution_ready=execution_ready,
        user_stream_status=user_status,
        halted=halted,
        halt_reason=store.halt_reason(),
        account_positions=len(positions),
        active_campaigns=len(records),
        active_orders=len(active_orders),
        active_algo_orders=len(active_algos),
        orphan_positions=orphan_positions,
        orphan_orders=orphan_orders,
        orphan_algo_orders=orphan_algos,
        missing_stops=tuple(sorted(missing_stops)),
        missing_take_profits=tuple(sorted(missing_tps)),
        runtime_stats=stats,
    )


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SignalGrid runtime health snapshot")
    parser.add_argument("--db", default="signalgrid.db")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)
    store = StateStore(Path(args.db))
    try:
        snapshot = collect_health(store)
        print(json.dumps(snapshot.to_dict(), indent=2 if args.pretty else None, sort_keys=True))
        return 0 if snapshot.healthy else 2
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
