from signalgrid.engine import SignalGridEngine
from signalgrid.market.binance_events import BinanceMarketEventRouter, EventResult
from signalgrid.models import Direction
from signalgrid.risk.engine import RiskDecision
from signalgrid.scanner import MultiSymbolScanner, ScannerConfig
from signalgrid.signals.engine import SignalEngine
from tests.test_signal_engine import make_state

class AlwaysApproveRisk:
    def decide(self, signal, positions):
        if signal.direction is Direction.PASS:
            return RiskDecision(False, "NO_VALID_SIGNAL")
        return RiskDecision(True, "APPROVED", 100.0, 3)

def scanner(now):
    router = BinanceMarketEventRouter()
    router.states["SOLUSDT"] = make_state(1)
    router.states["SOLUSDT"].last_event_time_ms = now[0]
    engine = SignalGridEngine(SignalEngine(), AlwaysApproveRisk())
    s = MultiSymbolScanner(router, engine=engine, config=ScannerConfig(min_evaluation_interval_ms=100, max_market_data_age_ms=1000, signal_debounce_ms=5000), now_ms=lambda: now[0])
    s.configure_symbols(["SOLUSDT"])
    return s

def test_symbol_cap_is_enforced():
    s = MultiSymbolScanner(BinanceMarketEventRouter(), config=ScannerConfig(max_symbols=50))
    try:
        s.configure_symbols([f"C{i}USDT" for i in range(51)])
    except ValueError as exc:
        assert "max_symbols=50" in str(exc)
    else:
        raise AssertionError("expected symbol cap")

def test_event_is_evaluated_and_emitted():
    now = [1_000_000]
    s = scanner(now)
    result = s.on_event(EventResult("SOLUSDT", "aggTrade", True))
    assert result is not None
    assert result.emitted
    assert result.signal.direction is Direction.LONG
    assert result.market_event_age_ms == 0
    assert result.compute_latency_ms >= 0

def test_evaluation_is_rate_limited():
    now = [1_000_000]
    s = scanner(now)
    assert s.on_event(EventResult("SOLUSDT", "aggTrade", True)) is not None
    now[0] += 50
    assert s.on_event(EventResult("SOLUSDT", "bookTicker", True)) is None

def test_stale_data_is_blocked():
    now = [1_000_000]
    s = scanner(now)
    s.router.state("SOLUSDT").last_event_time_ms = now[0] - 1001
    result = s.on_event(EventResult("SOLUSDT", "aggTrade", True))
    assert result is not None
    assert not result.emitted
    assert result.reason == "STALE_MARKET_DATA"

def test_duplicate_signal_is_debounced():
    now = [1_000_000]
    s = scanner(now)
    first = s.on_event(EventResult("SOLUSDT", "aggTrade", True))
    assert first and first.emitted
    now[0] += 150
    s.router.state("SOLUSDT").last_event_time_ms = now[0]
    second = s.on_event(EventResult("SOLUSDT", "aggTrade", True))
    assert second is not None
    assert not second.emitted
    assert second.reason == "SIGNAL_DEBOUNCE"


class NeverWakeRadar:
    def observe(self, state, now_ms):
        return None


def test_impulse_radar_can_keep_signal_hub_asleep():
    now = [1_000_000]
    s = scanner(now)
    s.impulse_radar = NeverWakeRadar()
    result = s.on_event(EventResult("SOLUSDT", "aggTrade", True))
    assert result is not None
    assert not result.emitted
    assert result.reason == "IMPULSE_RADAR_SLEEP"
