from unittest.mock import patch

import pandas as pd
import pytest

from config import CB_FILTERS
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


# ---------------------------------------------------------------------------
# Task 4: 粗筛 / 硬剔除 / 因子打分
# ---------------------------------------------------------------------------

from strategies.convertible import (
    prefilter,
    apply_hard_exclusions,
    score_bond,
    score_universe,
)


def _u(**kw):
    base = {
        "code": "113059", "name": "福莱转债", "stock_code": "605488",
        "stock_name": "福莱新材", "price": 115.31, "convert_price": 41.56,
        "convert_value": 22.06, "premium_rate": 422.63, "stock_price": 9.17,
        "rating": "AA", "issue_size": 10.0, "listed": True,
    }
    base.update(kw)
    return base


def test_prefilter_excludes_unlisted():
    assert prefilter([_u(listed=False)]) == []


def test_prefilter_excludes_st_stock():
    assert prefilter([_u(stock_name="*ST航图")]) == []
    assert prefilter([_u(stock_name="ST东时")]) == []


def test_prefilter_excludes_low_rating():
    assert prefilter([_u(rating="CC")]) == []
    assert prefilter([_u(rating="BB+")]) == []


def test_prefilter_excludes_extreme_premium():
    assert prefilter([_u(premium_rate=45.0)]) == []
    assert prefilter([_u(premium_rate=-10.0)]) == []


def test_prefilter_excludes_bad_price():
    assert prefilter([_u(price=75.0)]) == []
    assert prefilter([_u(price=250.0)]) == []


def test_prefilter_excludes_low_convert_value():
    assert prefilter([_u(convert_value=50.0)]) == []


def test_prefilter_excludes_small_issue_size():
    assert prefilter([_u(issue_size=0.2)]) == []


def test_prefilter_keeps_normal_bond():
    assert len(prefilter([_u(premium_rate=8.0, convert_value=97.0)])) == 1


def test_hard_exclusion_redeem_announced():
    rows = [_u(premium_rate=8.0, convert_value=97.0)]
    factors = {"113059": {"redeem_status": "已公告强赎", "redeem_dist": 50.0}}
    kept, dropped = apply_hard_exclusions(rows, factors)
    assert kept == []
    assert "113059" in dropped


def test_hard_exclusion_redeem_distance_below_3pct():
    rows = [_u(premium_rate=8.0, convert_value=97.0)]
    factors = {"113059": {"redeem_status": "", "redeem_dist": 2.0}}
    kept, dropped = apply_hard_exclusions(rows, factors)
    assert kept == []
    assert "113059" in dropped


def test_hard_exclusion_keeps_normal():
    rows = [_u(premium_rate=8.0, convert_value=97.0)]
    factors = {"113059": {"redeem_status": "", "redeem_dist": 25.0}}
    kept, dropped = apply_hard_exclusions(rows, factors)
    assert len(kept) == 1
    assert dropped == {}


def test_hard_exclusion_keeps_when_redeem_not_announced():
    # 「未公告强赎」「强赎条款」「临近到期」不能被误判为已公告
    rows = [_u(premium_rate=8.0, convert_value=97.0)]
    for status in ("未公告强赎", "强赎条款", "", "临近到期", "公告不强赎"):
        factors = {"113059": {"redeem_status": status, "redeem_dist": 25.0}}
        kept, dropped = apply_hard_exclusions(rows, factors)
        assert len(kept) == 1, status
        assert dropped == {}, status


def test_score_bond_floor_above_par():
    # bond_floor 是 PBV_RATIO（纯债价值/面值）。1.05 → 债底 105 元；
    # 债现价 100 元 → 保护度 1.05 ≥ 1 → 满分
    s = score_bond(_u(price=100.0, premium_rate=8.0, convert_value=97.0),
                   {"bond_floor": 1.05, "ytm": 2.0, "year_left": 3.5})
    assert s["a_score"] is not None
    assert s["factors"]["bond_floor"]["score"] == 100


def test_score_bond_floor_below_price_is_low():
    # 债底 85 元 vs 债现价 110 元 → 保护度 0.77 < 0.8 → 20 分
    s = score_bond(_u(price=110.0, premium_rate=8.0, convert_value=97.0),
                   {"bond_floor": 0.85, "ytm": 2.0, "year_left": 3.5})
    assert s["factors"]["bond_floor"]["score"] == 20


def test_score_premium_buckets():
    rows = [_u(premium_rate=8.0, convert_value=97.0)]
    s = score_bond(rows[0], {"bond_floor": 0.95, "ytm": 1.0, "year_left": 2.0})
    assert s["factors"]["premium"]["score"] == 90


def test_missing_factor_renormalizes_group():
    # rating 缺省时取中性 60（不计入缺失），price_level 恒可得，
    # 因此 A 组至少有 rating + price_level 两个因子参与
    rows = [_u(premium_rate=8.0, convert_value=97.0)]
    s = score_bond(rows[0], {"rating": "AA"})
    assert s["a_score"] is not None
    assert s["factor_counts"]["a"] == 2
    # bond_floor / ytm / year_left 缺失 → 不参与计分
    assert s["factors"]["bond_floor"] is None
    assert s["factors"]["ytm"] is None
    assert s["factors"]["year_left"] is None


def test_group_with_no_factors_is_none():
    # B 组至少 premium + convert_ratio 恒可得，故不会整组缺失
    rows = [_u(premium_rate=8.0, convert_value=97.0)]
    s = score_bond(rows[0], {"rating": "AA"})
    assert s["b_score"] is not None


def test_score_below_min_factors_is_excluded():
    # A 组恒有 price_level + rating 两个因子，故 score_bond 仍能出分。
    # 最低因子门槛在 score_universe 层执行：因子数不足则不进入榜单。
    rows = [_u(premium_rate=8.0, convert_value=97.0)]
    s = score_bond(rows[0], {})
    assert s["total_score"] is not None
    assert sum(s["factor_counts"].values()) >= CB_FILTERS["min_factors"]

    # score_universe 层：因子数 < min_factors 的被剔除
    thin = score_universe(rows, {})
    assert all(sum(x["factor_counts"].values()) >= CB_FILTERS["min_factors"] for x in thin)


# ---------------------------------------------------------------------------
# Task 5: 因子派生 + 仓位计算
# ---------------------------------------------------------------------------

from strategies.convertible import build_factor_map, size_shares_cb, build_pick_rows


def test_build_factor_map_computes_year_left_and_distances():
    rows = [_u(premium_rate=8.0, convert_value=97.0)]
    details = {
        "113059": {
            "bond_floor": 1.01,
            "expire_date": "2028-05-20",
            "redeem_trig_price": 54.03,
            "resale_trig_price": 29.09,
            "convert_price": 41.56,
            "stock_price": 9.17,
        }
    }
    today = "2026-10-24"
    fm = build_factor_map(rows, details, today=today)
    f = fm["113059"]
    assert f["year_left"] is not None
    assert 1.5 < f["year_left"] < 1.7
    # 强赎距离 = (54.03 / 9.17 - 1) * 100 ≈ 489%
    assert f["redeem_dist"] > 400
    # 回售距离 = (29.09 / 9.17 - 1) * 100 ≈ 217%
    assert f["put_dist"] > 200
    assert f["adj_count"] == 50


def test_build_factor_map_estimates_ytm():
    rows = [_u(price=100.0, premium_rate=8.0, convert_value=97.0)]
    details = {"113059": {"expire_date": "2028-05-20", "bond_floor": 0.98}}
    fm = build_factor_map(rows, details, today="2026-10-24")
    f = fm["113059"]
    assert f["ytm"] is not None
    assert f["ytm_estimated"] is True


def test_build_factor_map_handles_missing_expire_date():
    rows = [_u(premium_rate=8.0, convert_value=97.0)]
    details = {"113059": {"expire_date": None}}
    fm = build_factor_map(rows, details, today="2026-10-24")
    assert fm["113059"]["year_left"] is None
    assert fm["113059"]["ytm"] is None


def test_size_shares_cb_uses_lot_of_10():
    # 100000 * 0.15 / (115.31 * 10) = 13.0 手 → 130 张（13 × 10）
    assert size_shares_cb(100000.0, 0.15, 115.31) == 130
    # 100000 * 0.15 / (50.0 * 10) = 30 手 → 300 张
    assert size_shares_cb(100000.0, 0.15, 50.0) == 300
    # 张数必须是 10 的整数倍
    assert size_shares_cb(100000.0, 0.15, 137.77) % 10 == 0


def test_size_shares_cb_skips_if_less_than_one_lot():
    # 1000 * 0.15 / (200 * 10) = 0.075 手 → 不足 1 手
    assert size_shares_cb(1000.0, 0.15, 200.0) == 0


def test_build_pick_rows_ranked_and_capped():
    rows = [_u(code=f"code{i}", premium_rate=5.0 + i, convert_value=97.0)
            for i in range(15)]
    scored = score_universe(rows, {})
    picks = build_pick_rows(scored, top_n=10, capital=100000.0)
    assert len(picks) == 10
    assert picks[0]["rank"] == 1
    assert picks[0]["size_pct"] == 0.15
    assert picks[0]["stop_price"] < picks[0]["price"]
    assert picks[0]["tp_price"] > picks[0]["price"]
    assert picks[0]["shares"] % 10 == 0
    # rank 连续 1..N（无 skipped pick 造成的空洞）
    assert [p["rank"] for p in picks] == list(range(1, 11))


# ---------------------------------------------------------------------------
# Task 6: 退出预警
# ---------------------------------------------------------------------------

from strategies.convertible import evaluate_alerts, ALERT_LEVELS


def test_alert_emergency_on_redeem_announced():
    alerts = evaluate_alerts(
        positions=[{"code": "113059", "name": "福莱转债", "shares": 50,
                    "entry_price": 115.0, "stop_price": 105.8, "tp_price": 138.0}],
        quotes={"113059": {"price": 130.0, "redeem_status": "已公告强赎",
                           "redeem_dist": 20.0, "premium_rate": 5.0,
                           "stock_price": 30.0, "convert_price": 20.0}},
    )
    assert len(alerts) == 1
    assert alerts[0]["level"] == "emergency"
    assert "强赎" in alerts[0]["message"]


def test_alert_emergency_on_redeem_distance_below_3pct():
    alerts = evaluate_alerts(
        positions=[{"code": "113059", "name": "福莱转债", "shares": 50,
                    "entry_price": 115.0, "stop_price": 105.8, "tp_price": 138.0}],
        quotes={"113059": {"price": 130.0, "redeem_status": "",
                           "redeem_dist": 2.0, "premium_rate": 5.0,
                           "stock_price": 30.0, "convert_price": 20.0}},
    )
    assert alerts[0]["level"] == "emergency"


def test_alert_important_on_stop_loss():
    alerts = evaluate_alerts(
        positions=[{"code": "113059", "name": "福莱转债", "shares": 50,
                    "entry_price": 115.0, "stop_price": 110.0, "tp_price": 138.0}],
        quotes={"113059": {"price": 105.0, "redeem_status": "", "redeem_dist": 30.0,
                           "premium_rate": 5.0, "stock_price": 30.0,
                           "convert_price": 20.0}},
    )
    assert alerts[0]["level"] == "important"
    assert "止损" in alerts[0]["message"]


def test_alert_important_on_put_zone():
    alerts = evaluate_alerts(
        positions=[{"code": "113059", "name": "福莱转债", "shares": 50,
                    "entry_price": 115.0, "stop_price": 100.0, "tp_price": 138.0}],
        quotes={"113059": {"price": 108.0, "redeem_status": "", "redeem_dist": 30.0,
                           "premium_rate": 5.0, "stock_price": 13.0,
                           "convert_price": 20.0}},
    )
    assert alerts[0]["level"] == "important"
    assert "回售" in alerts[0]["message"]


def test_alert_watch_on_redeem_distance_3_to_8pct():
    alerts = evaluate_alerts(
        positions=[{"code": "113059", "name": "福莱转债", "shares": 50,
                    "entry_price": 115.0, "stop_price": 100.0, "tp_price": 138.0}],
        quotes={"113059": {"price": 108.0, "redeem_status": "", "redeem_dist": 5.0,
                           "premium_rate": 5.0, "stock_price": 30.0,
                           "convert_price": 20.0}},
    )
    assert alerts[0]["level"] == "watch"


def test_alert_watch_on_high_premium():
    alerts = evaluate_alerts(
        positions=[{"code": "113059", "name": "福莱转债", "shares": 50,
                    "entry_price": 115.0, "stop_price": 100.0, "tp_price": 138.0}],
        quotes={"113059": {"price": 180.0, "redeem_status": "", "redeem_dist": 30.0,
                           "premium_rate": 60.0, "stock_price": 30.0,
                           "convert_price": 20.0}},
    )
    assert alerts[0]["level"] == "watch"


def test_alert_normal_on_take_profit():
    alerts = evaluate_alerts(
        positions=[{"code": "113059", "name": "福莱转债", "shares": 50,
                    "entry_price": 115.0, "stop_price": 100.0, "tp_price": 130.0}],
        quotes={"113059": {"price": 135.0, "redeem_status": "", "redeem_dist": 30.0,
                           "premium_rate": 5.0, "stock_price": 30.0,
                           "convert_price": 20.0}},
    )
    assert alerts[0]["level"] == "normal"


def test_no_alert_for_healthy_position():
    alerts = evaluate_alerts(
        positions=[{"code": "113059", "name": "福莱转债", "shares": 50,
                    "entry_price": 115.0, "stop_price": 100.0, "tp_price": 138.0}],
        quotes={"113059": {"price": 118.0, "redeem_status": "", "redeem_dist": 30.0,
                           "premium_rate": 5.0, "stock_price": 30.0,
                           "convert_price": 20.0}},
    )
    assert alerts == []


def test_alert_no_false_positive_on_not_announced_redeem():
    # 「公告不强赎」「未公告强赎」不能触发 emergency
    alerts = evaluate_alerts(
        positions=[{"code": "113059", "name": "福莱转债", "shares": 50,
                    "entry_price": 115.0, "stop_price": 100.0, "tp_price": 138.0}],
        quotes={"113059": {"price": 118.0, "redeem_status": "公告不强赎",
                           "redeem_dist": 30.0, "premium_rate": 5.0,
                           "stock_price": 30.0, "convert_price": 20.0}},
    )
    assert alerts == []
