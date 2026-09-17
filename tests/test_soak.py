from signalgrid.ops.health import HealthSnapshot
from signalgrid.ops.soak import SoakSample, append_sample, evaluate_soak, load_samples


def _health(**overrides):
    data = dict(
        healthy=True,
        runtime_mode="TESTNET",
        runtime_status="TESTNET_LIVE",
        execution_ready=True,
        user_stream_status="LIVE",
        halted=False,
        halt_reason=None,
        account_positions=0,
        active_campaigns=0,
        active_orders=0,
        active_algo_orders=0,
        orphan_positions=(),
        orphan_orders=(),
        orphan_algo_orders=(),
        missing_stops=(),
        missing_take_profits=(),
        runtime_stats={"open_failures": 0, "signal_to_order_p95_ms": 125.0},
    )
    data.update(overrides)
    return HealthSnapshot(**data)


def _hourly_samples(hours=24):
    return [
        SoakSample(1_000 + hour * 3600 * 1000, _health().to_dict())
        for hour in range(hours + 1)
    ]


def test_clean_24h_soak_passes_only_with_continuous_coverage():
    report = evaluate_soak(_hourly_samples(24), 24 * 3600, max_gap_seconds=3601)
    assert report.passed
    assert report.unhealthy_samples == 0
    assert report.gap_violation_count == 0
    assert report.max_sample_gap_seconds == 3600
    assert report.max_open_failures == 0
    assert report.max_signal_to_order_p95_ms == 125.0


def test_24h_endpoints_with_large_missing_gap_fail():
    first = SoakSample(1_000, _health().to_dict())
    last = SoakSample(1_000 + 24 * 3600 * 1000, _health().to_dict())
    report = evaluate_soak([first, last], 24 * 3600, max_gap_seconds=180)
    assert not report.passed
    assert report.gap_violation_count == 1
    assert report.max_sample_gap_seconds == 24 * 3600


def test_short_soak_fails_even_if_every_sample_is_healthy():
    first = SoakSample(1_000, _health().to_dict())
    last = SoakSample(1_000 + 3600 * 1000, _health().to_dict())
    report = evaluate_soak([first, last], 24 * 3600, max_gap_seconds=3601)
    assert not report.passed
    assert report.observed_seconds == 3600


def test_any_protection_gap_or_open_failure_fails_soak():
    rows = _hourly_samples(24)
    bad = _health(
        healthy=False,
        missing_stops=("SOLUSDT",),
        runtime_stats={"open_failures": 1, "signal_to_order_p95_ms": 200.0},
    )
    rows[12] = SoakSample(rows[12].timestamp_ms, bad.to_dict())
    report = evaluate_soak(rows, 24 * 3600, max_gap_seconds=3601)
    assert not report.passed
    assert report.protection_gap_samples == 1
    assert report.max_open_failures == 1


def test_soak_journal_round_trip(tmp_path):
    path = tmp_path / "soak.jsonl"
    append_sample(path, _health(), timestamp_ms=1000)
    append_sample(path, _health(), timestamp_ms=2000)
    rows = load_samples(path)
    assert [row.timestamp_ms for row in rows] == [1000, 2000]
    assert rows[0].health["healthy"] is True
