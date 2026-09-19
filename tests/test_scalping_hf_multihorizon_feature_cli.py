from signalgrid.backtest.scalping_hf_multihorizon_feature_cli import HORIZONS


def test_multihorizon_feature_study_is_bounded():
    assert HORIZONS == (12, 36, 60)
    assert [h * 5 for h in HORIZONS] == [60, 180, 300]
