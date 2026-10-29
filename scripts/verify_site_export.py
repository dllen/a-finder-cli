#!/usr/bin/env python3
"""部署前校验导出产物是否完整。

背景：`open_db` 在库文件不存在时会**新建空库**，于是 `export_json.py` 会「成功」但产出
一个没有任何数据的站点。CI 的部署步骤带 `always()`，会把这样的空目录 force-push 到
gh-pages —— 2026-10-27 线上就是这么被清成只剩 `.nojekyll` 的。

为什么还要「不许比线上更旧」（--not-older-than）：2026-10-29 查明 CI 缓存库与本地库是
**两条独立谱系**（CI 的行情谱系断在 2026-10-13，站点却一直由本地库发布到 10-29）。
只校验「有没有数据」时，孤儿库导出的 dates.json=["2026-10-13"] + 十几个 JSON 一样能通过
`--min-dates 1 --min-json 10` 的门禁 —— CI 会把一个**比线上旧 16 天**的站点 force-push
上去，比 2026-10-27 的空站点更隐蔽（页面正常、数据却停在半个月前），而
`needs_fallback.py` 之后还会认为「线上已是最新」不再兜底。
单调性守卫把这种静默回退变成**步骤失败**：宁可这次不发布，也不能发布更旧的。

用法：
    python3 scripts/verify_site_export.py --site site [--min-dates 1] [--min-json 10]
    python3 scripts/verify_site_export.py --site site --not-older-than 2026-10-29
    python3 scripts/verify_site_export.py --site site --expect-latest 2026-10-29

退出码：0 = 通过；1 = 不完整或比线上更旧（禁止部署）；2 = 参数错误。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional


def _normalize_date(value: str) -> str:
    """日期规范化成 YYYY-MM-DD；无法解析时原样返回（交由调用方比较）。"""
    text = str(value).strip()
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return text


def check(
    site_dir: Path,
    min_dates: int = 1,
    min_json: int = 10,
    not_older_than: Optional[str] = None,
    expect_latest: Optional[str] = None,
) -> List[str]:
    """返回问题列表；空列表表示通过。"""
    problems: List[str] = []

    index = site_dir / "index.html"
    if not index.exists():
        problems.append(f"缺少 {index}")
    elif index.stat().st_size < 1000:
        problems.append(f"{index} 过小（{index.stat().st_size} 字节），疑似未渲染")

    dates_file = site_dir / "data" / "dates.json"
    latest = ""
    if not dates_file.exists():
        problems.append(f"缺少 {dates_file}")
    else:
        try:
            dates = json.loads(dates_file.read_text(encoding="utf-8")).get("dates") or []
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{dates_file} 解析失败: {exc}")
        else:
            if len(dates) < min_dates:
                problems.append(
                    f"{dates_file} 的 dates 只有 {len(dates)} 个（要求 >= {min_dates}）"
                    f"—— 导出没有拿到真实数据"
                )
            elif dates:
                latest = _normalize_date(dates[0])
                # 单调性守卫：产物最新日不得比线上更旧。
                # dates.json 是新→旧排列，dates[0] 就是最新日。
                if not_older_than:
                    floor = _normalize_date(not_older_than)
                    if latest < floor:
                        problems.append(
                            f"产物最新日 {latest} 比线上最新日 {floor} 更旧"
                            f"—— 禁止部署（发布会用旧数据覆盖更新的线上站点）"
                        )
                if expect_latest:
                    want = _normalize_date(expect_latest)
                    if latest < want:
                        problems.append(
                            f"产物最新日 {latest} 未达到期望交易日 {want}"
                            f"—— 行情可能未落地，禁止部署"
                        )

    data_dir = site_dir / "data"
    json_count = len(list(data_dir.glob("*.json"))) if data_dir.exists() else 0
    if json_count < min_json:
        problems.append(f"{data_dir} 下只有 {json_count} 个 JSON（要求 >= {min_json}）")

    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="校验站点导出产物")
    ap.add_argument("--site", default="site")
    ap.add_argument("--min-dates", type=int, default=1)
    ap.add_argument("--min-json", type=int, default=10)
    ap.add_argument(
        "--not-older-than", default=None,
        help="产物最新日不得早于该日期（通常是线上 dates.json 的最新日）",
    )
    ap.add_argument(
        "--expect-latest", default=None,
        help="产物最新日必须达到该日期（通常是期望交易日）",
    )
    args = ap.parse_args(argv)

    site_dir = Path(args.site)
    if not site_dir.exists():
        print(f"::error::站点目录不存在: {site_dir}", file=sys.stderr)
        return 1

    problems = check(
        site_dir, args.min_dates, args.min_json,
        not_older_than=args.not_older_than,
        expect_latest=args.expect_latest,
    )
    if problems:
        for p in problems:
            print(f"::error::{p}", file=sys.stderr)
        print("❌ 导出产物不完整或比线上更旧，禁止部署（避免清空/回退线上站点）", file=sys.stderr)
        return 1

    dates = json.loads((site_dir / "data" / "dates.json").read_text(encoding="utf-8"))["dates"]
    print(f"✅ 导出校验通过：{len(dates)} 个选股日期，最近 {dates[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
