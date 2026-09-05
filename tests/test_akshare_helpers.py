import pandas as pd
import pytest
from akshare_data_provider import _abstract_metric, _annual_metrics


def _make_df():
    return pd.DataFrame({
        "指标": ["毛利率", "毛利率"],
        "单位": ["%", "%"],
        "20241231": [42.5, 40.0],
        "20240630": [20.0, 18.0],
    })


def test_abstract_metric_returns_all_columns_by_default():
    df = _make_df()
    out = _abstract_metric(df, "毛利率")
    assert out == [42.5, 40.0, 20.0, 18.0]


def test_abstract_metric_filters_by_col():
    df = _make_df()
    out = _abstract_metric(df, "毛利率", col="20241231")
    assert out == [42.5, 40.0]


def test_abstract_metric_missing_name_returns_none():
    df = _make_df()
    assert _abstract_metric(df, "不存在") is None


def test_annual_metrics_only_keeps_december_columns():
    df = pd.DataFrame({
        "指标": ["毛利率"],
        "单位": ["%"],
        "20241231": [42.5],
        "20240930": [20.0],
        "20231231": [40.0],
        "20230930": [18.0],
    })
    out = _annual_metrics(df)
    assert set(out.keys()) == {2024, 2023}
    assert out[2024]["gross_margin"] == pytest.approx(42.5)


def test_annual_metrics_extracts_gross_margin_and_roe_excl():
    df = pd.DataFrame({
        "指标": ["毛利率", "净资产收益率(扣非)"],
        "单位": ["%", "%"],
        "20241231": [45.0, 18.0],
        "20231231": [43.0, 17.0],
    })
    out = _annual_metrics(df)
    assert out[2024]["gross_margin"] == pytest.approx(45.0)
    assert out[2024]["roe_excl"] == pytest.approx(18.0)
    assert out[2023]["roe_excl"] == pytest.approx(17.0)


def test_annual_metrics_empty_or_invalid_returns_empty():
    assert _annual_metrics(None) == {}
    assert _annual_metrics(pd.DataFrame()) == {}
