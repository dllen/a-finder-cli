#!/usr/bin/env python3
"""hs300.db 的谱系存储：用 GitHub Release 资产替代 actions/cache。

为什么换掉 actions/cache（2026-11-04 实测）
------------------------------------------
CI 的行情谱系原来靠 actions/cache 传递，实测**双向都不工作**：

- 缓存条目停在 2026-10-27 / 10-29 两条 5.2 MB 的残库，此后再没更新过；
- Post Save 一直是 skipped（步骤报 success 但什么都没存）；
- Post Restore 也是 skipped（没还原出库）；
- 于是 gap-heal 拿到的是**空库**：体检 30 天全 0 只、补抓后历史深度 0 根；
- 缓存总用量才 ~202 MB，远低于 10 GB 配额 ⇒ 不是配额问题。

改成 release 资产后有三个直接好处：

1. **单一名义资产 + 覆盖语义**：不用「哪个 run 的 key 最新」这种隐式规则，
   读的就是那个名字的文件，行为可预测。
2. **不占 actions/cache 的 10 GB 配额**，也不再受「cache 已存在就不能覆盖」限制。
3. **可以加单调性守卫**：写入前比对线上 meta，库比线上旧就**拒绝覆盖** ——
   原来 cache 是无条件 last-writer-wins，谱系回退就是这么来的。

设计
----
- Release tag 固定 db-lineage（不是每个 run 一个，避免无限增长）。
- 两个资产：
  - hs300.db.gz        —— 压缩后的库（gzip，stdlib，不引入新依赖）
  - lineage-meta.json  —— 元数据：最新行情日 / 历史深度 / 交易日数 / 是否合格
- restore：下载两个资产，解压到 --db。没有资产 ⇒ 退出码 2（按冷启动处理）。
- save：先做**资格判定**（见 decide_save），通过才覆盖。
  不合格的库**绝不覆盖**已有的谱系根（等价于原来的 degraded- 前缀隔离）。

用法
----
    GH_TOKEN=... GITHUB_REPOSITORY=owner/repo python3 scripts/db_lineage.py restore --db hs300.db
    GH_TOKEN=... GITHUB_REPOSITORY=owner/repo python3 scripts/db_lineage.py save --db hs300.db --lineage-ok true
    python3 scripts/db_lineage.py show --db hs300.db          # 只看本地元数据，不联网

退出码：0 成功；1 出错；2 restore 时「没有可用的谱系」（正常冷启动）。
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import shutil
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

#: 谱系所在的 release tag。固定一个，不要按 run 生成。
LINEAGE_TAG = "db-lineage"
#: 数据库资产名（gzip 压缩）。
DB_ASSET = "hs300.db.gz"
#: 元数据资产名。
META_ASSET = "lineage-meta.json"
#: 与 db_freshness / pick_history 保持一致的历史深度门槛。
MIN_HISTORY_BARS = 221


# ---------------------------------------------------------------------------
# 本地元数据
# ---------------------------------------------------------------------------

def read_local_meta(db_path: str):
    """读本地库的关键指纹（不联网，纯 sqlite 查询）。"""
    import sqlite3

    if not os.path.exists(db_path):
        return {"exists": False}
    conn = sqlite3.connect(db_path)
    try:
        def one(sql, default=None):
            try:
                row = conn.execute(sql).fetchone()
            except sqlite3.Error:
                return default
            return row[0] if row and row[0] is not None else default

        max_date = one("SELECT MAX(trade_date) FROM daily_prices")
        bars = one(
            "SELECT MIN(n) FROM (SELECT COUNT(*) n FROM daily_prices GROUP BY code)", 0
        )
        tradings = one("SELECT COUNT(DISTINCT trade_date) FROM daily_prices", 0)
        codes = one("SELECT COUNT(DISTINCT code) FROM daily_prices", 0)
        rows = one("SELECT COUNT(*) FROM daily_prices", 0)
    finally:
        conn.close()
    return {
        "exists": True,
        "max_trade_date": max_date,
        "min_bars_per_code": int(bars or 0),
        "trading_days": int(tradings or 0),
        "codes": int(codes or 0),
        "price_rows": int(rows or 0),
        "size_bytes": os.path.getsize(db_path),
    }


def decide_save(local: dict, remote, lineage_ok: bool):
    """是否允许用本地库覆盖谱系根。返回 (是否覆盖, 原因)。

    这是整个方案的核心判定，**纯函数**，便于测试。

    规则（顺序有意义）：
    1. 库不存在 / 没有行情 -> 不覆盖。
    2. lineage_ok 为假（有缺口、体检红、历史不够）-> **绝不覆盖**。
       等价于原来 cache 方案里把不合格的库写到 degraded- 前缀：
       一个坏库不该成为下一次 run 的起点。
    3. 历史深度 < 221 -> 不覆盖（pick_history 会 0 只票，短历史会一直传递）。
    4. 线上 meta 存在且**更新**（remote.max_trade_date > local）-> 不覆盖。
       这就是 cache 方案缺失的单调性守卫：并发/迟到的 runner 不能把谱系倒退。
    5. 线上 meta 存在且线上历史更深 -> 不覆盖（别用过短的库替换长的）。
    6. 其余情况允许覆盖。
    """
    if not local.get("exists"):
        return False, "本地库不存在"
    if not local.get("max_trade_date"):
        return False, "本地库没有行情"
    if not lineage_ok:
        return False, "本次没通过谱系资格（有缺口/体检红/历史不足），不覆盖已有谱系根"
    if local.get("min_bars_per_code", 0) < MIN_HISTORY_BARS:
        return False, (
            "历史深度不足（%s < %d 根），pick_history 会 0 只票，不覆盖"
            % (local.get("min_bars_per_code"), MIN_HISTORY_BARS)
        )
    if remote:
        r_max = remote.get("max_trade_date")
        if r_max and local["max_trade_date"] < r_max:
            return False, (
                "本地最新日 %s 比线上 %s 旧，拒绝回退谱系（单调性守卫）"
                % (local["max_trade_date"], r_max)
            )
        r_bars = int(remote.get("min_bars_per_code") or 0)
        if r_bars > local.get("min_bars_per_code", 0):
            return False, (
                "本地历史深度 %s 比线上 %s 浅，拒绝用更短的库替换"
                % (local.get("min_bars_per_code"), r_bars)
            )
    return True, "通过：最新日 %s，历史 %s 根" % (
        local["max_trade_date"],
        local.get("min_bars_per_code"),
    )


# ---------------------------------------------------------------------------
# GitHub API（只用 stdlib）
# ---------------------------------------------------------------------------

def _api() -> str:
    return os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")


def _repo() -> str:
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if not repo or "/" not in repo:
        raise RuntimeError("GITHUB_REPOSITORY 未设置（应为 owner/repo）")
    return repo


def _token() -> str:
    token = (os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or "").strip()
    if not token:
        raise RuntimeError("GH_TOKEN / GITHUB_TOKEN 未设置")
    return token


def _request(method: str, url: str, data=None, headers=None, raw: bool = False):
    base = {
        "Authorization": "Bearer %s" % _token(),
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        # Cloudflare 会 403 掉 urllib 默认 UA（本仓库踩过），必须显式带
        "User-Agent": "a-finder-db-lineage/1.0",
    }
    if headers:
        base.update(headers)
    req = urllib.request.Request(url, data=data, headers=base, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            payload = resp.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        detail = exc.read().decode("utf-8", "ignore")[:400]
        raise RuntimeError("GitHub API %s %s -> HTTP %s: %s" % (method, url, exc.code, detail))
    if raw:
        return payload
    if not payload:
        return {}
    return json.loads(payload.decode("utf-8", "ignore"))


def get_release():
    """取 db-lineage release；不存在返回 None。"""
    return _request("GET", "%s/repos/%s/releases/tags/%s" % (_api(), _repo(), LINEAGE_TAG))


def ensure_release():
    """取或创建 db-lineage release。"""
    rel = get_release()
    if rel:
        return rel
    body = json.dumps(
        {
            "tag_name": LINEAGE_TAG,
            "name": "hs300.db lineage",
            "target_commitish": "main",
            "body": (
                "hs300.db 的行情谱系存储（替代 actions/cache）。"
                "由 scripts/db_lineage.py 自动维护；资产会被原地覆盖，请勿手工编辑。"
            ),
        }
    ).encode("utf-8")
    return _request("POST", "%s/repos/%s/releases" % (_api(), _repo()), data=body)


def find_asset(release, name: str):
    for asset in (release or {}).get("assets", []) or []:
        if asset.get("name") == name:
            return asset
    return None


def download_asset(asset) -> bytes:
    url = "%s/repos/%s/releases/assets/%s" % (_api(), _repo(), asset["id"])
    return _request(
        "GET", url, headers={"Accept": "application/octet-stream"}, raw=True
    )


def delete_asset(asset) -> None:
    _request(
        "DELETE",
        "%s/repos/%s/releases/assets/%s" % (_api(), _repo(), asset["id"]),
    )


def _uploads_base() -> str:
    """资产上传走独立域名：api.github.com -> uploads.github.com（GHES 同域名）。"""
    api = _api()
    if "://api." in api:
        return api.replace("://api.", "://uploads.", 1)
    return api


def upload_asset(release, name: str, payload: bytes) -> dict:
    url = "%s/repos/%s/releases/%s/assets?%s" % (
        _uploads_base(),
        _repo(),
        release["id"],
        urllib.parse.urlencode({"name": name}),
    )
    return _request(
        "POST",
        url,
        data=payload,
        headers={"Content-Type": "application/gzip"},
    )


# ---------------------------------------------------------------------------
# restore / save / show
# ---------------------------------------------------------------------------

def restore(db_path: str) -> int:
    """下载谱系资产并解压。返回 0 成功、2 没有谱系、1 出错。"""
    rel = get_release()
    if not rel:
        print("没有 db-lineage release —— 按冷启动处理（rc=2）")
        return 2
    db_asset = find_asset(rel, DB_ASSET)
    if not db_asset:
        print("release 里没有 %s —— 按冷启动处理（rc=2）" % DB_ASSET)
        return 2

    meta_asset = find_asset(rel, META_ASSET)
    if meta_asset:
        try:
            meta = json.loads(download_asset(meta_asset).decode("utf-8", "ignore"))
        except Exception as exc:  # noqa: BLE001
            print("::warning::meta 资产读取失败（忽略）：%s" % exc)
            meta = {}
        print(
            "线上谱系 meta：最新行情日=%s 历史深度=%s 交易日=%s 合格=%s"
            % (
                meta.get("max_trade_date"),
                meta.get("min_bars_per_code"),
                meta.get("trading_days"),
                meta.get("lineage_ok"),
            )
        )

    tmpdir = tempfile.mkdtemp()
    try:
        gz_path = os.path.join(tmpdir, DB_ASSET)
        with open(gz_path, "wb") as fh:
            fh.write(download_asset(db_asset))
        # gzip 解压到大文件：用流式，别整包读进内存
        with gzip.open(gz_path, "rb") as src, open(db_path, "wb") as dst:
            shutil.copyfileobj(src, dst, length=1024 * 1024)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    local = read_local_meta(db_path)
    print(
        "已恢复 %s：%.1f MB，最新行情日=%s，历史深度=%s 根，交易日=%s"
        % (
            db_path,
            os.path.getsize(db_path) / 1048576.0,
            local.get("max_trade_date"),
            local.get("min_bars_per_code"),
            local.get("trading_days"),
        )
    )
    return 0


def save(db_path: str, lineage_ok: bool) -> int:
    """按 decide_save 的判定决定是否覆盖谱系根。返回 0（跳过也算成功）/1 出错。"""
    local = read_local_meta(db_path)
    if not local.get("exists"):
        print("::warning::本地没有 %s，不保存谱系" % db_path)
        return 0

    remote = None
    rel = get_release()
    if rel:
        meta_asset = find_asset(rel, META_ASSET)
        if meta_asset:
            try:
                remote = json.loads(download_asset(meta_asset).decode("utf-8", "ignore"))
            except Exception as exc:  # noqa: BLE001
                print("::warning::线上 meta 读取失败，按「无线上谱系」处理：%s" % exc)
                remote = None

    ok, reason = decide_save(local, remote, lineage_ok)
    print("保存判定：%s —— %s" % ("覆盖" if ok else "跳过", reason))
    if not ok:
        return 0

    rel = ensure_release()
    payload_meta = dict(local)
    payload_meta["lineage_ok"] = True
    meta_bytes = json.dumps(payload_meta, ensure_ascii=False, indent=1).encode("utf-8")

    tmpdir = tempfile.mkdtemp()
    try:
        gz_path = os.path.join(tmpdir, DB_ASSET)
        with open(db_path, "rb") as src, gzip.open(gz_path, "wb", compresslevel=6) as dst:
            shutil.copyfileobj(src, dst, length=1024 * 1024)
        gz_size = os.path.getsize(gz_path)
        print(
            "压缩完成：%.1f MB -> %.1f MB"
            % (local["size_bytes"] / 1048576.0, gz_size / 1048576.0)
        )

        # 先删旧资产再传新的：同名资产不能重复，且 DELETE+POST 比 PATCH 更稳。
        for name in (META_ASSET, DB_ASSET):
            old = find_asset(rel, name)
            if old:
                delete_asset(old)
        upload_asset(rel, META_ASSET, meta_bytes)
        upload_asset(rel, DB_ASSET, open(gz_path, "rb").read())
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    print(
        "已覆盖谱系根：最新行情日=%s，历史深度=%s 根"
        % (local.get("max_trade_date"), local.get("min_bars_per_code"))
    )
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="hs300.db 谱系存储（GitHub Release 资产）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_restore = sub.add_parser("restore", help="从 release 恢复 hs300.db")
    p_restore.add_argument("--db", default="hs300.db")

    p_save = sub.add_parser("save", help="把 hs300.db 存成谱系根（带资格判定）")
    p_save.add_argument("--db", default="hs300.db")
    p_save.add_argument(
        "--lineage-ok",
        default="false",
        help="本次是否通过谱系资格（true/false，来自体检/门禁步骤）",
    )

    p_show = sub.add_parser("show", help="只看本地库元数据（不联网）")
    p_show.add_argument("--db", default="hs300.db")

    args = ap.parse_args(argv)

    try:
        if args.cmd == "restore":
            return restore(args.db)
        if args.cmd == "save":
            return save(args.db, args.lineage_ok.strip().lower() == "true")
        if args.cmd == "show":
            print(json.dumps(read_local_meta(args.db), ensure_ascii=False, indent=1))
            return 0
    except Exception as exc:  # noqa: BLE001
        print("::error::db_lineage %s 失败：%s" % (args.cmd, exc))
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
