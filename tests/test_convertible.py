from unittest.mock import patch

import pandas as pd
import pytest

from cb_data_provider import (
    fetch_universe_snapshot,
    fetch_bond_detail,
    fetch_redeem_status_map,
)


def _cov_df(rows):
    return pd.DataFrame(rows)


def test_fetch_universe_snapshot_maps_columns():
    df = _cov_df([
        {
            "债券代码": "113059", "债券简称": "福莱转债",
            "正股代码": "605488", "正股简称": "福莱新材",
            "正股价": 9.17, "转股价": 41.56, "转股价值": 22.06,
            "债现价": 115.31, "转股溢价率": 422.63,
            "发行规模": 10.0, "信用评级": "AA", "上市时间": pd.Timestamp("2023-07-10"),
        },
    ])
    with patch("cb_data_provider.ak.bond_zh_cov", return_value=df):
        rows = fetch_universe_snapshot()
    assert len(rows) == 1
    r = rows[0]
    assert r["code"] == "113059"
    assert r["name"] == "福莱转债"
    assert r["stock_code"] == "605488"
    assert r["price"] == 115.31
    assert r["convert_price"] == 41.56
    assert r["premium_rate"] == 422.63
    assert r["rating"] == "AA"
    assert r["listed"] is True


def test_fetch_universe_snapshot_marks_unlisted():
    df = _cov_df([
        {
            "债券代码": "127120", "债券简称": "千红转债",
            "正股代码": "002550", "正股简称": "千红制药",
            "正股价": 6.27, "转股价": 6.42, "转股价值": 97.66,
            "债现价": 100.0, "转股溢价率": 2.39,
            "发行规模": 10.0, "信用评级": "AA", "上市时间": pd.NaT,
        },
    ])
    with patch("cb_data_provider.ak.bond_zh_cov", return_value=df):
        rows = fetch_universe_snapshot()
    assert rows[0]["listed"] is False


def test_fetch_bond_detail_reads_pbv_ratio_and_expire():
    df = pd.DataFrame([{
        "SECURITY_NAME_ABBR": "福莱转债",
        "PBV_RATIO": 1.01,
        "EXPIRE_DATE": "2028-05-20 00:00:00",
        "RATING": "AA",
        "REDEEM_TRIG_PRICE": 54.03,
        "RESALE_TRIG_PRICE": 29.09,
        "TRANSFER_PRICE": 41.56,
        "CONVERT_STOCK_PRICE": 9.17,
        "TRANSFER_VALUE": 22.0645,
        "CURRENT_BOND_PRICE": "115.315",
    }])
    with patch("cb_data_provider.ak.bond_zh_cov_info", return_value=df):
        d = fetch_bond_detail("113059")
    assert d["code"] == "113059"
    assert d["bond_floor"] == 1.01
    assert d["expire_date"] == "2028-05-20"
    assert d["redeem_trig_price"] == 54.03
    assert d["resale_trig_price"] == 29.09


def test_fetch_bond_detail_tolerates_none_fields():
    df = pd.DataFrame([{
        "SECURITY_NAME_ABBR": "已退市",
        "PBV_RATIO": None,
        "EXPIRE_DATE": None,
        "RATING": "AA",
        "REDEEM_TRIG_PRICE": None,
        "RESALE_TRIG_PRICE": None,
        "TRANSFER_PRICE": None,
        "CONVERT_STOCK_PRICE": None,
        "TRANSFER_VALUE": None,
        "CURRENT_BOND_PRICE": None,
    }])
    with patch("cb_data_provider.ak.bond_zh_cov_info", return_value=df):
        d = fetch_bond_detail("113050")
    assert d["bond_floor"] is None
    assert d["expire_date"] is None
    assert d["redeem_trig_price"] is None


def test_fetch_bond_detail_handles_dash_price():
    df = pd.DataFrame([{
        "SECURITY_NAME_ABBR": "千红转债",
        "PBV_RATIO": 2.93,
        "EXPIRE_DATE": "2032-10-27 00:00:00",
        "RATING": "AA",
        "REDEEM_TRIG_PRICE": 8.35,
        "RESALE_TRIG_PRICE": 4.49,
        "TRANSFER_PRICE": 6.42,
        "CONVERT_STOCK_PRICE": 6.27,
        "TRANSFER_VALUE": 97.6636,
        "CURRENT_BOND_PRICE": "-",
    }])
    with patch("cb_data_provider.ak.bond_zh_cov_info", return_value=df):
        d = fetch_bond_detail("127120")
    assert d["current_bond_price"] is None
