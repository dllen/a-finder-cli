#!/usr/bin/env python3
"""刷新 agent：在本机轮询 Worker 的刷新任务队列，跑完流水线回报状态。

为什么是「拉模式」：
  Worker 跑在 Cloudflare 边缘，**够不到本机的 127.0.0.1**；反过来本机可以出网
  （它本来就要 push gh-pages）。所以由本机主动去 Worker 领任务，而不是 Worker 推过来。
  代价是几秒的感知延迟，换来少一个常驻隧道进程、少一个故障点。

  本 agent **只做出站请求**，不监听任何端口：Worker 也从不需要访问本机。

职责边界：只做「领任务 → 调 a-finder.sh → 回报状态」。**不含任何业务计算**
（选股/计划/导出逻辑都在既有 Python 模块里，不在这里重复实现）。

配置（环境变量）：
  REFRESH_WORKER_BASE    Worker 基址，默认 https://ycu.space
  REFRESH_AGENT_SECRET   与 Worker 的 REFRESH_AGENT_SECRET 一致（必需）
  REFRESH_POLL_SECONDS   轮询间隔秒数，默认 3
  REFRESH_DB             SQLite 路径，默认 hs300.db
  REFRESH_SITE_DIR       站点产物目录，默认 site
  REFRESH_LOG            日志路径，默认 logs/refresh.log
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

ROOT = Path(__file__).resolve().parent

WORKER_BASE = os.environ.get("REFRESH_WORKER_BASE", "https://ycu.space").rstrip("/")
AGENT_SECRET = os.environ.get("REFRESH_AGENT_SECRET", "")
POLL_SECONDS = float(os.environ.get("REFRESH_POLL_SECONDS", "3"))
DB_PATH = os.environ.get("REFRESH_DB", str(ROOT / "hs300.db"))
SITE_DIR = Path(os.environ.get("REFRESH_SITE_DIR", str(ROOT / "site")))
LOG_PATH = Path(os.environ.get("REFRESH_LOG", str(ROOT / "logs" / "refresh.log")))

# 阶段提示：回报给前端的 message，用户看得到
MSG_SYNC = "正在同步行情并计算选股/计划（通常 10~30 分钟）"
MSG_PUBLISH = "正在导出并发布到站点"

# 认证失败时轮询要退避，不能按正常节奏继续打 Worker（既刷不出任务，
# 又把「secret 配错了」这个真问题淹没在日志里）。
AUTH_BACKOFF_SECONDS = 60.0

# 自定义 User-Agent：**不能用 urllib 默认的 Python-urllib/3.11**。
# 2026-11-03 实测：Cloudflare 直接 403 掉默认 UA，请求到不了 Worker
# （表现是「secret 配对了却一直 403」，很难联想到 UA）。
USER_AGENT = "a-finder-agent/1.0"


class AgentAuthError(RuntimeError):
    """Worker 返回 401 —— REFRESH_AGENT_SECRET 没配或不匹配。

    单独一个异常类型，是为了让主循环能把它和「没任务」「网络抖动」区分开：
    401 是**配置错误**，必须显式退避并提示，不能当成「暂时没任务」继续轮询。
    """


def latest_date_from_site(site_dir: Path) -> Optional[str]:
    """读 site/data/dates.json 的最新日；读不到返回 None。

    dates.json 是**新→旧**排列，dates[0] 就是最新日。
    任何异常都返回 None —— agent 不能因为读不到产物就崩掉。
    """
    try:
        payload = json.loads(
            (Path(site_dir) / "data" / "dates.json").read_text(encoding="utf-8")
        )
    except Exception:
        return None
    dates = payload.get("dates") or []
    return dates[0] if dates else None


def summarize_result(returncode: int, latest_date: Optional[str], log_tail: str) -> Dict[str, str]:
    """把「子进程退出码 + 产物日期 + 日志尾巴」汇总成结果摘要。

    退出码 0 但没有产物日期 → **failed**。
    这条不能省：将来若某条路径意外返回 0 又没产物，页面不能显示"成功"
    却什么都没更新。
    """
    tail = (log_tail or "").strip()
    if returncode == 0 and latest_date:
        return {
            "status": "success",
            "latest_date": latest_date,
            "message": f"已更新，最新数据 {latest_date}",
        }
    if returncode == 0:
        return {
            "status": "failed",
            "latest_date": "",
            "message": "流水线退出码为 0 但没有产出数据日期，判定为失败",
        }
    return {
        "status": "failed",
        "latest_date": "",
        "message": f"流水线失败（退出码 {returncode}）" + (f"：{tail[-300:]}" if tail else ""),
    }


def build_result_payload(
    job_id: str,
    status: str,
    latest_date: Optional[str],
    message: str,
) -> Dict[str, Any]:
    """构造 POST /api/refresh/result 的 payload（字段名与 Worker 约定一致）。"""
    return {
        "job_id": job_id,
        "status": status,
        "latest_date": latest_date or "",
        "message": message or "",
    }


def _http(url: str, *, method: str = "GET", headers: Optional[Dict[str, str]] = None,
          body: Optional[Dict[str, Any]] = None, timeout: int = 20) -> Dict[str, Any]:
    """发一个 JSON 请求，返回解析后的 dict。

    401 抛 AgentAuthError（配置错误，要显式退避）；
    其它 HTTP 错误与网络异常照常抛，由调用方兜。

    ⚠️ **必须带自定义 User-Agent**（2026-11-03 实测）：Cloudflare 会直接
    403 掉 urllib 默认的 `Python-urllib/3.11` —— 请求**到不了 Worker**。
    表现是「secret 明明配对了却一直 403」，极难联想到 UA 上。
    （用 curl 测同一个端点会正常返回，因为 curl 的 UA 不在屏蔽名单里。）
    """
    data = None
    hdrs = dict(headers or {})
    # 别删这一行：默认 UA 会被 Cloudflare 403，agent 直接废掉。
    hdrs.setdefault("User-Agent", USER_AGENT)
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise AgentAuthError(
                "Worker 认证失败（401）：REFRESH_AGENT_SECRET 与 Worker 的同名 secret 不一致"
            ) from exc
        if exc.code == 403:
            # 403 通常不是 Worker 给的（Worker 认证失败走 401），而是 Cloudflare
            # 挡在前面。最常见的两个原因：UA 被拦、或 WAF 规则命中。
            raise AgentAuthError(
                "被 Cloudflare 拦截（403）：不是 Worker 返回的。"
                "请确认 User-Agent 不是默认的 Python-urllib（会被屏蔽）"
            ) from exc
        raise
    return json.loads(raw) if raw else {}


def fetch_task() -> Optional[str]:
    """向 Worker 领一个任务；没有任务返回 None。

    认证失败（401）**不吞**：它会往上抛成 AgentAuthError，让主循环退避。
    把它当成「没任务」会导致每 3 秒刷一次 Worker 却永远领不到任务，
    而真问题（secret 配错）被日志噪声淹没。
    """
    try:
        payload = _http(
            f"{WORKER_BASE}/api/refresh/next",
            headers={"X-Agent-Secret": AGENT_SECRET},
            timeout=15,
        )
    except AgentAuthError as exc:
        print(f"[agent] {exc}", flush=True)
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"[agent] 领任务失败: {exc}", flush=True)
        return None
    return payload.get("job_id") or None


def report_result(payload: Dict[str, Any], retries: int = 3) -> bool:
    """把结果回报给 Worker。返回是否成功。

    要重试的理由：回报失败 = Worker 那边这行还停在 running，
    用户看到的是**永远转圈**（要等 60 分钟的僵尸任务清理才变 failed）。
    而流水线其实已经跑完并发布成功了 —— 这时候 agent 自己重试几下
    就能把状态补上，比让用户白等一小时强得多。
    """
    for attempt in range(1, retries + 1):
        try:
            _http(
                f"{WORKER_BASE}/api/refresh/result",
                method="POST",
                headers={"X-Agent-Secret": AGENT_SECRET},
                body=payload,
                timeout=20,
            )
            return True
        except Exception as exc:  # noqa: BLE001
            print(f"[agent] 回报失败（第 {attempt}/{retries} 次）: {exc}", flush=True)
            if attempt < retries:
                time.sleep(2.0 * attempt)
    print("[agent] 回报最终失败：该任务在 Worker 侧会停在 running 直到超时清理", flush=True)
    return False


def _run_pipeline() -> Tuple[int, str]:
    """跑 `a-finder.sh site --push`，返回 (退出码, 日志尾巴)。

    用 caffeinate 包裹：Mac 常开但合盖会睡，睡眠会中断同步/回补，
    留下一个半成品产物。
    """
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    # caffeinate 不存在时直接跑，不要因为缺它就完全不刷新
    if shutil.which("caffeinate"):
        argv = ["caffeinate", "-i", "bash", "a-finder.sh", "site", DB_PATH, "20", str(SITE_DIR), "--push"]
    else:
        argv = ["bash", "a-finder.sh", "site", DB_PATH, "20", str(SITE_DIR), "--push"]

    with LOG_PATH.open("a", encoding="utf-8") as log:
        log.write(f"\n=== refresh 开始 {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
        log.write(f"$ {' '.join(argv)}\n")
        log.flush()
        try:
            proc = subprocess.run(
                argv, cwd=str(ROOT), stdout=log, stderr=subprocess.STDOUT,
                env={**os.environ},
            )
            rc = proc.returncode
        except Exception as exc:  # noqa: BLE001
            log.write(f"\n启动失败: {exc}\n")
            log.flush()
            return 1, f"启动失败: {exc}"
    tail = _read_log_tail()
    return rc, tail


def _read_log_tail(n: int = 4000) -> str:
    """读日志末尾若干字符（给用户看失败原因用）。"""
    try:
        text = LOG_PATH.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    return text[-n:]


def main() -> int:
    if not AGENT_SECRET:
        print(
            "缺少 REFRESH_AGENT_SECRET 环境变量。"
            "它必须与 Worker 的同名 secret 一致（wrangler secret put REFRESH_AGENT_SECRET）。",
            file=sys.stderr,
        )
        return 2

    print(f"[agent] 启动：Worker={WORKER_BASE} 轮询={POLL_SECONDS}s", flush=True)
    busy = False
    auth_backoff_until = 0.0

    while True:
        try:
            if not busy and time.monotonic() >= auth_backoff_until:
                job_id = fetch_task()
                if job_id:
                    busy = True
                    print(f"[agent] 领到任务 {job_id}", flush=True)
                    print(f"[agent] {MSG_SYNC}", flush=True)
                    rc, tail = _run_pipeline()
                    latest = latest_date_from_site(SITE_DIR)
                    result = summarize_result(rc, latest, tail)
                    payload = build_result_payload(
                        job_id=job_id,
                        status=result["status"],
                        latest_date=result["latest_date"],
                        message=result["message"],
                    )
                    report_result(payload)
                    print(f"[agent] 任务 {job_id} 完成：{result['status']} {result['message']}", flush=True)
                    busy = False
        except AgentAuthError:
            # secret 不对是配置错误，继续按 3 秒轮询只会刷爆 Worker 日志。
            # 退避 60 秒并明确提示，让人去修配置而不是盯着日志发呆。
            auth_backoff_until = time.monotonic() + AUTH_BACKOFF_SECONDS
            print(
                f"[agent] 认证失败，退避 {AUTH_BACKOFF_SECONDS:.0f}s 后重试"
                "（请确认 Worker 已设置 REFRESH_AGENT_SECRET 且与本机一致）",
                flush=True,
            )
        except KeyboardInterrupt:
            print("\n[agent] 收到中断，退出", flush=True)
            return 0
        except Exception as exc:  # noqa: BLE001
            print(f"[agent] 循环异常: {exc}", flush=True)
            busy = False
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    raise SystemExit(main())
