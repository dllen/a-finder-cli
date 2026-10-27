#!/usr/bin/env python3
"""部署前校验导出产物是否完整。

背景：`open_db` 在库文件不存在时会**新建空库**，于是 `export_json.py` 会「成功」但产出
一个没有任何数据的站点。CI 的部署步骤带 `always()`，会把这样的空目录 force-push 到
gh-pages —— 2026-10-27 线上就是这么被清成只剩 `.nojekyll` 的。

用法：
    python3 scripts/verify_site_export.py --site site [--min-dates 1] [--min-json 10]

退出码：0 = 通过；1 = 不完整（禁止部署）；2 = 参数错误。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List


def check(site_dir: Path, min_dates: int = 1, min_json: int = 10) -> List[str]:
    """返回问题列表；空列表表示通过。"""
    problems: List[str] = []

    index = site_dir / "index.html"
    if not index.exists():
        problems.append(f"缺少 {index}")
    elif index.stat().st_size < 1000:
        problems.append(f"{index} 过小（{index.stat().st_size} 字节），疑似未渲染")

    dates_file = site_dir / "data" / "dates.json"
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
    args = ap.parse_args(argv)

    site_dir = Path(args.site)
    if not site_dir.exists():
        print(f"::error::站点目录不存在: {site_dir}", file=sys.stderr)
        return 1

    problems = check(site_dir, args.min_dates, args.min_json)
    if problems:
        for p in problems:
            print(f"::error::{p}", file=sys.stderr)
        print("❌ 导出产物不完整，禁止部署（避免清空线上站点）", file=sys.stderr)
        return 1

    dates = json.loads((site_dir / "data" / "dates.json").read_text(encoding="utf-8"))["dates"]
    print(f"✅ 导出校验通过：{len(dates)} 个选股日期，最近 {dates[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
