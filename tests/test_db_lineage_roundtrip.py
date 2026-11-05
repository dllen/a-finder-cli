"""db_lineage 的**端到端**往返测试：用本地假 GitHub API 跑 restore→save→restore。

**为什么需要这个测试**
----------------------
单测覆盖了 decide_save 的判定规则，但**没有验证过**「真的能存进去、真的能取回来」
—— 而这正是用户要求的核心：「把 DB 存成 artifact/release asset，每次 restore 时下载」。

CI 里没法直接验证（读不到 job 日志、artifact 下载要认证），所以这里起一个
**本地 HTTP 服务**冒充 GitHub Releases API，把整条链路跑通：

    save（空 release → 建 release → 删旧资产 → 上传 gz + meta）
    restore（列 release → 下载 gz → 解压 → 得到一个可查询的 sqlite）
    save 第二次（覆盖语义：资产被原地替换，不累积）

服务端只实现脚本用到的 5 个端点。GITHUB_API_URL 指向本地时，
`_uploads_base()` 会原样返回（不含 `://api.`，所以不会改写成 uploads.github.com），
上传与读取都打到同一个假服务上。
"""

import gzip
import json
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

from scripts import db_lineage


class FakeGitHub:
    """最小可用的 GitHub Releases API 假实现（内存态）。"""

    def __init__(self):
        self.releases = []          # [{id, tag_name, assets: [{id, name, data}]}]
        self._next_id = 1
        self.uploads = []           # 记录上传顺序，便于断言「先删后传」

    def add_asset(self, release, name, data):
        rid = self._next_id
        self._next_id += 1
        asset = {"id": rid, "name": name, "data": data,
                 "size_in_bytes": len(data), "url": "http://x/%d" % rid}
        release["assets"].append(asset)
        return asset


def _make_handler(gh):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _json(self, code, payload):
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _raw(self, code, data):
            self.send_response(code)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _find_release_by_tag(self, tag):
            for r in gh.releases:
                if r["tag_name"] == tag:
                    return r
            return None

        def do_GET(self):
            u = urlparse(self.path)
            parts = [p for p in u.path.split("/") if p]
            # GET /repos/{o}/{r}/releases/tags/{tag}
            if len(parts) == 6 and parts[0] == "repos" and parts[3] == "releases" and parts[4] == "tags":
                rel = self._find_release_by_tag(parts[5])
                if rel is None:
                    return self._json(404, {"message": "Not Found"})
                return self._json(200, {"id": rel["id"], "tag_name": rel["tag_name"],
                                        "assets": [{"id": a["id"], "name": a["name"],
                                                    "size_in_bytes": a["size_in_bytes"],
                                                    "url": a["url"]} for a in rel["assets"]]})
            # GET /repos/{o}/{r}/releases/assets/{id}
            if len(parts) == 6 and parts[0] == "repos" and parts[3] == "releases" and parts[4] == "assets":
                aid = int(parts[5])
                for rel in gh.releases:
                    for a in rel["assets"]:
                        if a["id"] == aid:
                            return self._raw(200, a["data"])
                return self._json(404, {"message": "Not Found"})
            return self._json(404, {"message": "Not Found", "path": u.path})

        def do_POST(self):
            u = urlparse(self.path)
            parts = [p for p in u.path.split("/") if p]
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            # POST /repos/{o}/{r}/releases  → 建 release
            if len(parts) == 4 and parts[0] == "repos" and parts[3] == "releases":
                payload = json.loads(body or b"{}")
                rel = {"id": gh._next_id, "tag_name": payload.get("tag_name"),
                       "assets": []}
                gh._next_id += 1
                gh.releases.append(rel)
                return self._json(201, {"id": rel["id"], "tag_name": rel["tag_name"],
                                        "assets": []})
            # POST /repos/{o}/{r}/releases/{rid}/assets?name=...
            if len(parts) == 6 and parts[3] == "releases" and parts[5] == "assets":
                rid = int(parts[4])
                name = (parse_qs(u.query).get("name") or [""])[0]
                rel = next((r for r in gh.releases if r["id"] == rid), None)
                if rel is None:
                    return self._json(404, {"message": "no release"})
                # 同名资产不允许重复：真实 GitHub 会 422，这里也拒绝
                if any(a["name"] == name for a in rel["assets"]):
                    return self._json(422, {"message": "already_exists"})
                a = gh.add_asset(rel, name, body)
                gh.uploads.append(name)
                return self._json(201, {"id": a["id"], "name": a["name"]})
            return self._json(404, {"message": "Not Found", "path": u.path})

        def do_DELETE(self):
            u = urlparse(self.path)
            parts = [p for p in u.path.split("/") if p]
            if len(parts) == 6 and parts[3] == "releases" and parts[4] == "assets":
                aid = int(parts[5])
                for rel in gh.releases:
                    for i, a in enumerate(rel["assets"]):
                        if a["id"] == aid:
                            rel["assets"].pop(i)
                            self.send_response(204)
                            self.end_headers()
                            return
                return self._json(404, {"message": "Not Found"})
            return self._json(404, {"message": "Not Found"})

    return Handler


@pytest.fixture
def fake_gh(monkeypatch):
    gh = FakeGitHub()
    server = HTTPServer(("127.0.0.1", 0), _make_handler(gh))
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("GITHUB_API_URL", "http://127.0.0.1:%d" % port)
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setenv("GH_TOKEN", "fake-token")
    yield gh
    server.shutdown()


def _make_db(path, dates=250, codes=("000001", "000002")):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE daily_prices (code TEXT, trade_date TEXT, close REAL)")
    # 瘦身脚本会查这三张表（真实库一定有）：
    # daily_prices（行情，只查不删）/ daily_picks（选股，只查不删）/ trade_plan（被删）
    conn.execute("CREATE TABLE daily_picks (pick_date TEXT, code TEXT)")
    conn.execute("INSERT INTO daily_picks VALUES ('2026-11-04', '000001')")
    conn.execute("CREATE TABLE trade_plan (plan_date TEXT, portfolio TEXT, code TEXT)")
    for i in range(200):
        conn.execute("INSERT INTO trade_plan VALUES (?,?,?)",
                     ("2026-%02d-%02d" % (1 + i // 28, 1 + i % 28), "default", "000001"))
    for code in codes:
        for i in range(dates):
            conn.execute("INSERT INTO daily_prices VALUES (?,?,?)",
                         (code, "2026-%02d-%02d" % (1 + i // 28, 1 + i % 28), 10.0))
    conn.commit()
    conn.close()
    return path


def test_roundtrip_save_then_restore(tmp_path, fake_gh):
    """**这是用户要求的那个能力**：存成 release 资产，restore 时下载回来。"""
    src = _make_db(str(tmp_path / "src.db"))

    # 1) 空 release ⇒ save 应当自己建 release 并上传两个资产
    assert db_lineage.save(src, lineage_ok=True) == 0
    assert len(fake_gh.releases) == 1, "save 应当创建 db-lineage release"
    rel = fake_gh.releases[0]
    assert rel["tag_name"] == db_lineage.LINEAGE_TAG
    names = [a["name"] for a in rel["assets"]]
    assert db_lineage.DB_ASSET in names and db_lineage.META_ASSET in names

    # 2) restore 应当把 gz 解压成可查询的库
    out = str(tmp_path / "out.db")
    assert db_lineage.restore(out) == 0
    meta = db_lineage.read_local_meta(out)
    assert meta["price_rows"] == 500, "restore 出来的库必须完整（2 只 × 250 天）"
    assert meta["codes"] == 2
    assert meta["min_bars_per_code"] == 250


def test_meta_asset_is_readable_json(tmp_path, fake_gh):
    src = _make_db(str(tmp_path / "src.db"))
    db_lineage.save(src, lineage_ok=True)
    asset = next(a for a in fake_gh.releases[0]["assets"]
                 if a["name"] == db_lineage.META_ASSET)
    meta = json.loads(asset["data"].decode())
    assert meta["lineage_ok"] is True
    assert meta["min_bars_per_code"] == 250
    assert meta["max_trade_date"]


def test_second_save_overwrites_instead_of_accumulating(tmp_path, fake_gh):
    """覆盖语义：第二次保存必须**删旧再传新**，资产不累积。"""
    db1 = _make_db(str(tmp_path / "d1.db"), dates=250)
    db_lineage.save(db1, lineage_ok=True)
    first_assets = list(fake_gh.releases[0]["assets"])
    assert len(first_assets) == 2

    db2 = _make_db(str(tmp_path / "d2.db"), dates=260)
    db_lineage.save(db2, lineage_ok=True)
    rel = fake_gh.releases[0]
    assert len(rel["assets"]) == 2, "覆盖后仍应只有 2 个资产（不是 4 个）"
    # meta 反映的是**第二份**库
    meta = json.loads(next(a for a in rel["assets"]
                           if a["name"] == db_lineage.META_ASSET)["data"].decode())
    assert meta["min_bars_per_code"] == 260


def test_disqualified_save_touches_nothing(tmp_path, fake_gh):
    """不合格的库：release 都不该建（一个写请求都不发）。"""
    src = _make_db(str(tmp_path / "src.db"), dates=250)
    assert db_lineage.save(src, lineage_ok=False) == 0
    assert fake_gh.releases == [], "不合格时不该创建 release"
    assert fake_gh.uploads == [], "不合格时不该上传任何资产"


def test_restore_without_release_reports_cold_start(tmp_path, fake_gh):
    """没有 release ⇒ rc=2（= 正常冷启动），不是错误。"""
    out = str(tmp_path / "out.db")
    assert db_lineage.restore(out) == 2
    assert not (tmp_path / "out.db").exists()


def test_restore_roundtrips_after_slim(tmp_path, fake_gh):
    """瘦身之后存/取仍然完整 —— CI 是先瘦身再 save 的。"""
    from scripts.slim_db_for_cache import slim

    src = _make_db(str(tmp_path / "src.db"), dates=250)
    slim(src, src, keep_days=45)   # out_path == db_path ⇒ 原地瘦身
    db_lineage.save(src, lineage_ok=True)

    out = str(tmp_path / "out.db")
    assert db_lineage.restore(out) == 0
    meta = db_lineage.read_local_meta(out)
    assert meta["price_rows"] == 500, "瘦身只删 trade_plan，行情必须一行不少"
