"""JS 版对比逻辑（decide_compare.js）与 Python 版（decide_service）必须一致。

用 node 跑同一组 fixture，逐项比对 deviations 的 (severity, kind, code) 序列。
"""
import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
JS_DIR = ROOT / "static" / "js"

CFG = {
    "total_capital": 100000.0,
    "stock_ratio": 0.8,
    "cb_ratio": 0.2,
    "max_positions": 8,
    "max_single_weight": 0.10,
    "max_stock_exposure": 0.75,
    "cb_capital": 20000.0,
    "max_cb_positions": 3,
}

CASES = [
    # name, plan, positions, cb, prices
    ("perfect",
     [{"code": "600519", "name": "茅台", "action": "buy", "plan_price": 100.0,
       "shares": 100, "stop_price": 95.0, "tp_price": 110.0}],
     [{"code": "600519", "name": "茅台", "shares": 100,
       "entry_price": 100.0, "stop_price": 95.0}],
     [], {"600519": 100.0}),
    ("missing_buy",
     [{"code": "600519", "name": "茅台", "action": "buy", "plan_price": 100.0,
       "shares": 100, "stop_price": 95.0, "tp_price": 110.0},
      {"code": "600036", "name": "招行", "action": "buy", "plan_price": 40.0,
       "shares": 200, "stop_price": 38.0, "tp_price": 44.0}],
     [{"code": "600519", "name": "茅台", "shares": 100,
       "entry_price": 100.0, "stop_price": 95.0}],
     [], {"600519": 100.0}),
    ("empty_portfolio",
     [{"code": "600519", "name": "茅台", "action": "buy", "plan_price": 100.0,
       "shares": 100, "stop_price": 95.0, "tp_price": 110.0}],
     [], [], {}),
    ("over_position_limit",
     [], [{"code": f"60000{i}", "name": f"股{i}", "shares": 10,
           "entry_price": 10.0, "stop_price": 9.0} for i in range(9)],
     [], {}),
    ("stop_breached",
     [{"code": "600519", "name": "茅台", "action": "buy", "plan_price": 100.0,
       "shares": 100, "stop_price": 95.0, "tp_price": 110.0}],
     [{"code": "600519", "name": "茅台", "shares": 100,
       "entry_price": 100.0, "stop_price": 95.0}],
     [], {"600519": 90.0}),
    ("cb_over",
     [], [], [{"code": "113059", "name": "转债A", "shares": 200,
              "entry_price": 110.0, "stop_price": None}], {}),
]


def _run_js(plan, positions, cb, prices):
    script = """
import { computeComparison } from './decide_compare.js';
const input = JSON.parse(process.argv[1]);
const out = computeComparison(input.plan, input.positions, input.cb, input.prices, input.cfg);
process.stdout.write(JSON.stringify(out));
"""
    fixture = json.dumps({
        "plan": plan, "positions": positions, "cb": cb,
        "prices": prices, "cfg": CFG,
    })
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", script, fixture],
        cwd=str(JS_DIR), capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, f"node 失败: {proc.stderr}"
    return json.loads(proc.stdout)


@pytest.mark.parametrize("name,plan,positions,cb,prices", CASES)
def test_js_matches_python(name, plan, positions, cb, prices):
    from decide_service import compare_plan_vs_actual

    py = compare_plan_vs_actual(plan, positions, cb, prices, CFG)
    js = _run_js(plan, positions, cb, prices)

    py_sig = [(d["severity"], d["kind"], d["code"]) for d in py["deviations"]]
    js_sig = [(d["severity"], d["kind"], d["code"]) for d in js["deviations"]]
    assert js_sig == py_sig, f"{name}: deviations 不一致\npy={py_sig}\njs={js_sig}"

    assert js["stock_used"] == pytest.approx(py["stock_used"], abs=0.01)
    assert js["cb_used"] == pytest.approx(py["cb_used"], abs=0.01)
    assert js["cash"] == pytest.approx(py["cash"], abs=0.01)
    assert len(js["pending_build"]) == len(py["pending_build"])