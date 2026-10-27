#!/usr/bin/env python3
"""校验 schema 里的建表语句是否正好是期望的那几张。

用法：
    python3 scripts/check_schema_tables.py --schema worker/schema.sql \
        --expect real_positions,real_cb_positions

退出码：0 = 符合；1 = 不符。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


def tables_in(schema_text: str) -> list[str]:
    return re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", schema_text)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="校验 schema 建表清单")
    ap.add_argument("--schema", default="worker/schema.sql")
    ap.add_argument("--expect", required=True, help="逗号分隔的期望表名")
    args = ap.parse_args(argv)

    path = Path(args.schema)
    if not path.exists():
        print(f"::error::schema 不存在: {path}", file=sys.stderr)
        return 1

    found = sorted(tables_in(path.read_text(encoding="utf-8")))
    expect = sorted(t.strip() for t in args.expect.split(",") if t.strip())
    print(f"实际: {found}")
    print(f"期望: {expect}")
    if found != expect:
        print("❌ 建表清单不符", file=sys.stderr)
        return 1
    print("✅ schema 符合预期")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
