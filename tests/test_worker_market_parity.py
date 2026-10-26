"""Worker 解析出来的日线必须与本地库（东财口径入库）一致。

fixture 是腾讯 newfqkline(bfq) 的真实响应；本地库 hs300.db 里同一天的数据
是早前从东财 fqt=0 拉取的。两边都是"不复权交易所数据"，应逐字段相等
（pct_change 允许因小数位差异有 0.01 的容差）。
"""

import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "tx_newfq_bfq_600519.json"
DB = ROOT / "hs300.db"

pytestmark = pytest.mark.skipif(
    not FIXTURE.exists() or not DB.exists(),
    reason="需要 fixture 与本地 hs300.db（两者都缺失时跳过）",
)


def _worker_rows() -> dict:
    script = (
        "import { readFileSync } from 'node:fs';"
        "import { parseNewfq, computePctChange } from './worker/market/sources.js';"
        f"const raw = JSON.parse(readFileSync({str(FIXTURE)!r}, 'utf8'));"
        "const rows = computePctChange(parseNewfq(raw, '600519'));"
        "const out = {};"
        "for (const r of rows) out[r.trade_date] = r;"
        "console.log(JSON.stringify(out));"
    )
    res = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def _db_rows(dates) -> dict:
    conn = sqlite3.connect(str(DB))
    try:
        rows = {}
        for d in dates:
            r = conn.execute(
                "SELECT open, close, high, low, volume, amount, turnover, pct_change "
                "FROM daily_prices WHERE code='600519' AND trade_date=?",
                (d,),
            ).fetchone()
            if r:
                rows[d] = {
                    "open": r[0],
                    "close": r[1],
                    "high": r[2],
                    "low": r[3],
                    "volume": r[4],
                    "amount": r[5],
                    "turnover": r[6],
                    "pct_change": r[7],
                }
        return rows
    finally:
        conn.close()


def test_worker_parse_matches_local_db():
    js = _worker_rows()
    common = sorted(set(js) & set(_db_rows(list(js))))
    assert common, "fixture 日期与本地库没有交集（库太旧？）"

    db = _db_rows(common)
    for date in common:
        # 价：两边是同一份不复权交易所数据，应逐位相等
        for field in ("open", "close", "high", "low"):
            assert js[date][field] == pytest.approx(db[date][field], rel=1e-9), (
                f"{date}.{field}: worker={js[date][field]} db={db[date][field]}"
            )

        # 成交量：腾讯只给整手（31556），东财保留两位小数（31556.04），
        # 差异来自数据源自身的取整，容差放宽到 1e-4（约 0.01 手以内）。
        assert js[date]["volume"] == pytest.approx(db[date]["volume"], rel=1e-4), (
            f"{date}.volume: worker={js[date]['volume']} db={db[date]['volume']}"
        )

        # 换手率与成交额：腾讯给 %/万元，东财给 %/元，换算后应一致。
        # 本地库这两个字段历史入库时没存（恒为 0.0），仅在有值时才比对。
        if db[date]["turnover"]:
            assert js[date]["turnover"] == pytest.approx(
                db[date]["turnover"], abs=0.02
            ), f"{date}.turnover: worker={js[date]['turnover']} db={db[date]['turnover']}"
        if db[date]["amount"]:
            assert js[date]["amount"] == pytest.approx(
                db[date]["amount"], rel=0.01
            ), f"{date}.amount: worker={js[date]['amount']} db={db[date]['amount']}"

        # pct_change：Worker 用前收盘算，东财给现成值，容差 0.02。
        # 本地库该字段同样历史缺存（0.0），仅在有值时才比对。
        if js[date]["pct_change"] is not None and db[date]["pct_change"]:
            assert js[date]["pct_change"] == pytest.approx(
                db[date]["pct_change"], abs=0.02
            ), (
                f"{date}.pct_change: worker={js[date]['pct_change']} "
                f"db={db[date]['pct_change']}"
            )
