from signalgrid.ops.run_scalping_paper import build_scalping_paper_runtime
from signalgrid.runtime import RuntimeMode
from signalgrid.sonar.impulse_radar import ImpulseRadar


def test_canonical_scalping_paper_uses_locked_chain(tmp_path):
    runtime = build_scalping_paper_runtime(("BTCUSDT",), str(tmp_path / "paper.db"))
    try:
        assert runtime.config.mode is RuntimeMode.PAPER
        assert isinstance(runtime.scanner.impulse_radar, ImpulseRadar)
        assert runtime.store.get_runtime("coin_sonar_v2") == "ENABLED"
        assert runtime.store.get_runtime("execution_mode") == "PAPER_ONLY"
        assert runtime.paper_broker is not None
        assert runtime.campaign_executor is None
    finally:
        runtime.store.close()
