import pandas as pd
import pytest
from akshare_data_provider import _abstract_metric


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
