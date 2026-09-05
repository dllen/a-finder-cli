import pytest
from db_repository import FundamentalsHistoryRow
from strategies.linyuan_multi_factor import (
    LinYuanConfig, passes_linyuan_filter,
)

CFG = LinYuanConfig()


def _h(gm, roe, years):
    return [FundamentalsHistoryRow(
        code="X", year=y, gross_margin=gm, roe_excl=roe,
        report_date=f"{y}-12-31", synced_at="",
    ) for y in years]


def test_passes_with_5_consecutive_years():
    history = _h(0.50, 0.20, [2020, 2021, 2022, 2023, 2024])
    assert passes_linyuan_filter("医药生物", history, CFG) is True


def test_fails_when_only_4_years():
    history = _h(0.50, 0.20, [2020, 2021, 2022, 2023])
    assert passes_linyuan_filter("医药生物", history, CFG) is False


def test_fails_with_year_gap():
    # 缺 2022
    history = _h(0.50, 0.20, [2020, 2021, 2023, 2024])
    history.append(FundamentalsHistoryRow(
        code="X", year=2022, gross_margin=0, roe_excl=0,
        report_date="2022-12-31", synced_at="",
    ))
    assert passes_linyuan_filter("医药生物", history, CFG) is False


def test_fails_when_margin_below_threshold():
    history = _h(0.39, 0.20, [2020, 2021, 2022, 2023, 2024])
    assert passes_linyuan_filter("医药生物", history, CFG) is False


def test_fails_when_roe_below_threshold():
    history = _h(0.50, 0.14, [2020, 2021, 2022, 2023, 2024])
    assert passes_linyuan_filter("医药生物", history, CFG) is False


def test_fails_when_sector_not_in_whitelist():
    history = _h(0.50, 0.20, [2020, 2021, 2022, 2023, 2024])
    assert passes_linyuan_filter("房地产", history, CFG) is False


@pytest.mark.parametrize("sector", [
    "医药生物", "中药", "食品饮料", "机械设备", "电力设备", "汽车整车",
])
def test_all_whitelisted_sectors_pass(sector):
    history = _h(0.50, 0.20, [2020, 2021, 2022, 2023, 2024])
    assert passes_linyuan_filter(sector, history, CFG) is True
