#!/usr/bin/env python3
"""三层数据缺口体检：行情(L1) / 选股(L2) / 计划(L3)。

基准是 chinese_calendar 算出的真实交易日（默认近 30 天），**完全独立于库内数据**。
这是与 scripts/check_gaps.py 的根本差异：后者以库内已有日期为基准，因此
「库里有 10-27 而缺 10-26」时永远看不见 10-26（2026-10-27 实际踩过这个坑）。

层级关系：L1 缺时只报 L1，不报派生的 L2/L3（没有行情算不出选股）。

用法：
    python3 scripts/audit_coverage.py --db hs300.db
    python3 scripts/audit_coverage.py --db hs300.db --window-days 30 --json

退出码：0 = 无缺口；1 = 有缺口；2 = 无法判定（日历不可用/库读取失败/报告写不出来）。
"""

# 注意：这里**不能**加 `from __future__ import annotations`。
# dataclasses 在字段注解是字符串时会去 sys.modules 里查本模块（校验 ClassVar/KW_ONLY），
# 而测试用 importlib exec_module 直接加载本文件、不注册 sys.modules，于是拿到 None 报
# AttributeError。本模块的注解在 3.11 下都能求值，无需该 future import。
import argparse
import datetime as dt
import json
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from market_calendar import trading_days  # noqa: E402

WINDOW_DAYS = 30
MIN_CODES = 280
EXPECTED_PORTFOLIOS = ("5W", "10W", "15W", "20W", "25W", "30W",
                       "35W", "40W", "45W", "50W", "default")


class CalendarUnavailable(RuntimeError):
    """交易日历不可用，无法判定基准。"""


@dataclass(frozen=True)
class Gap:
    date: str
    layer: str          # "L1" | "L2" | "L3"
    detail: str

    def to_dict(self) -> Dict[str, str]:
        return {"date": self.date, "layer": self.layer, "detail": self.detail}

    @classmethod
    def from_dict(cls, payload: Dict) -> "Gap":
        """只取认识的键：报告里多出字段不算坏，少了必需字段才 KeyError。"""
        return cls(date=payload["date"], layer=payload["layer"],
                   detail=payload["detail"])


@dataclass
class Report:
    today: str
    window_days: int
    expected_dates: List[str] = field(default_factory=list)
    gaps: List[Gap] = field(default_factory=list)
    suppressed: List[Gap] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.gaps

    def by_layer(self) -> Dict[str, List[str]]:
        out: Dict[str, List[str]] = {}
        for g in self.gaps:
            out.setdefault(g.layer, []).append(g.date)
        return out

    def to_dict(self) -> Dict:
        return {
            "today": self.today,
            "ok": self.ok,
            "window_days": self.window_days,
            "expected_dates": self.expected_dates,
            "gaps": [g.to_dict() for g in self.gaps],
            "suppressed": [g.to_dict() for g in self.suppressed],
        }

    @classmethod
    def from_dict(cls, payload: Dict) -> "Report":
        """`to_dict()` 的逆操作，供修复脚本消费 `--out` 报告。

        不要手搓 `Gap(**g)`：那样 Gap 一加字段就是 TypeError（读侧契约只做一半）。
        """
        return cls(
            today=payload["today"],
            window_days=payload["window_days"],
            expected_dates=list(payload.get("expected_dates") or []),
            gaps=[Gap.from_dict(g) for g in payload.get("gaps") or []],
            suppressed=[Gap.from_dict(g) for g in payload.get("suppressed") or []],
        )


def _read_layer_counts(db_path: str, since: str):
    """读三层的覆盖情况。返回 (行情每日只数, 选股有行的日期, 计划每日档位集合)。"""
    conn = sqlite3.connect(db_path)
    try:
        prices = {
            d: n for d, n in conn.execute(
                "SELECT trade_date, COUNT(DISTINCT code) FROM daily_prices"
                " WHERE trade_date >= ? GROUP BY trade_date", (since,))
        }
        picks = {
            r[0] for r in conn.execute(
                "SELECT DISTINCT date FROM daily_picks WHERE date >= ?", (since,))
        }
        plans: Dict[str, set] = {}
        for d, pf in conn.execute(
            "SELECT DISTINCT plan_date, portfolio FROM trade_plan WHERE plan_date >= ?",
            (since,),
        ):
            plans.setdefault(d, set()).add(pf)
    finally:
        conn.close()
    return prices, picks, plans


def audit(
    db_path: str,
    *,
    today: Optional[dt.date] = None,
    window_days: int = WINDOW_DAYS,
    min_codes: int = MIN_CODES,
) -> Report:
    """三层体检。基准来自交易日历，与库内数据无关。"""
    today = today or dt.date.today()
    start = today - dt.timedelta(days=window_days)
    expected = trading_days(start, today)
    if not expected:
        raise CalendarUnavailable(
            "交易日历不可用（chinese_calendar 未安装或该年份无数据），无法判定基准")

    if not Path(db_path).exists():
        raise FileNotFoundError(f"数据库不存在: {db_path}")

    prices, picks, plans = _read_layer_counts(db_path, start.isoformat())
    report = Report(today=today.isoformat(), window_days=window_days,
                    expected_dates=expected)

    for d in expected:
        # L1 行情
        codes = prices.get(d, 0)
        if codes < min_codes:
            report.gaps.append(Gap(d, "L1", f"行情只有 {codes} 只（阈值 {min_codes}）"))
            # L1 缺 → L2/L3 判定无意义，记 suppressed 备查，不报
            if d not in picks:
                report.suppressed.append(Gap(d, "L2", "无选股（派生自 L1 缺失）"))
            missing_pf = [p for p in EXPECTED_PORTFOLIOS if p not in plans.get(d, set())]
            if missing_pf:
                report.suppressed.append(
                    Gap(d, "L3", f"缺档位 {','.join(missing_pf)}（派生自 L1 缺失）"))
            continue

        # L2 选股：只看有没有行（行数天然波动，不设阈值）
        if d not in picks:
            report.gaps.append(Gap(d, "L2", "无选股数据"))
            continue   # 选股没有 → 计划同样无从生成

        # L3 计划：每个应有档位都要有行
        have = plans.get(d, set())
        missing_pf = [p for p in EXPECTED_PORTFOLIOS if p not in have]
        if missing_pf:
            report.gaps.append(Gap(d, "L3", f"缺档位 {','.join(missing_pf)}"))

    return report


def _print_human(report: Report) -> None:
    total = len(report.expected_dates)
    print(f"体检区间：最近 {report.window_days} 天，应有 {total} 个交易日")
    if report.ok:
        print(f"✅ 三层无缺口（{report.expected_dates[0]} ~ {report.expected_dates[-1]}）")
        return
    print(f"❌ 发现 {len(report.gaps)} 处缺口：")
    for g in report.gaps:
        print(f"   [{g.layer}] {g.date} — {g.detail}")
    if report.suppressed:
        print(f"   （另有 {len(report.suppressed)} 处派生缺口因 L1 缺失未单独统计）")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="三层数据缺口体检")
    ap.add_argument("--db", default="hs300.db")
    ap.add_argument("--today", default=None, help="基准日 YYYY-MM-DD（默认今天，测试用）")
    ap.add_argument("--window-days", type=int, default=WINDOW_DAYS)
    ap.add_argument("--min-codes", type=int, default=MIN_CODES)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", default=None, help="把 JSON 报告写入该文件")
    args = ap.parse_args(argv)

    today = dt.date.fromisoformat(args.today) if args.today else None
    try:
        report = audit(args.db, today=today, window_days=args.window_days,
                       min_codes=args.min_codes)
    except CalendarUnavailable as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 2
    except (FileNotFoundError, sqlite3.Error) as exc:
        print(f"::error::读取数据库失败: {exc}", file=sys.stderr)
        return 2

    payload = json.dumps(report.to_dict(), ensure_ascii=False, indent=2)
    if args.out:
        # 报告必须落盘：写不出来 = 无法判定（rc=2），与「日历不可用/库读不了」同级。
        # 绝不能先打印「✅ 三层无缺口」再崩 —— 下游（修复步/通知步）会把
        # 「报告读不到」当成「没有缺口」，最后发出一条绿色心跳（2026-10-28 评审复现）。
        try:
            Path(args.out).write_text(payload, encoding="utf-8")
        except OSError as exc:
            print(f"::error::写入体检报告失败: {exc}", file=sys.stderr)
            return 2

    if args.json:
        print(payload)
    else:
        _print_human(report)

    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
