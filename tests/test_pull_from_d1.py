"""GH 侧从 Worker 拉增量写 SQLite。用注入的 fetcher 代替真实 HTTP。"""

import importlib.util
from pathlib import Path

import pytest

from db_repository import open_db

_SPEC = importlib.util.spec_from_file_location(
    "pull_from_d1",
    Path(__file__).resolve().parent.parent / "scripts" / "pull_from_d1.py",
)
pull = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(pull)


def _row(code, date, close, pct=0.0, turnover=0.0):
    return {
        "code": code,
        "trade_date": date,
        "open": close,
        "close": close,
        "high": close,
        "low": close,
        "volume": 1000,
        "amount": 1e6,
        "turnover": turnover,
        "pct_change": pct,
    }


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "m.db")
    conn = open_db(path)
    conn.execute(
        "INSERT INTO daily_prices (code, trade_date, open, close, high, low,"
        " volume, amount) VALUES ('600519','2026-10-22',100,100,100,100,1,1)"
    )
    conn.commit()
    yield path, conn
    conn.close()


def _fetcher(payloads):
    calls = []

    def fetch_json(url, token):
        calls.append(url)
        if "/api/market/health" in url:
            return payloads["health"]
        if "/api/market/dates" in url:
            return payloads["dates"]
        if "/api/market/day/" in url:
            date = url.rsplit("/", 1)[1]
            return payloads["days"].get(date, {"date": date, "rows": []})
        raise AssertionError(f"unexpected url {url}")

    fetch_json.calls = calls
    return fetch_json


def test_pull_writes_missing_days(db):
    path, conn = db
    fetch_json = _fetcher(
        {
            "health": {"latest_date": "2026-10-23"},
            "dates": {"dates": ["2026-10-23"]},
            "days": {
                "2026-10-23": {
                    "date": "2026-10-23",
                    "rows": [_row("600519", "2026-10-23", 110.0, 10.0)],
                }
            },
        }
    )
    result = pull.pull(path, "https://w", "t", max_days=60, fetch_json=fetch_json)
    assert result["written"] == 1
    assert result["fell_back"] is False
    got = conn.execute(
        "SELECT close FROM daily_prices WHERE code='600519' AND trade_date='2026-10-23'"
    ).fetchone()
    assert got[0] == 110.0


def test_pull_is_idempotent(db):
    path, conn = db
    payloads = {
        "health": {"latest_date": "2026-10-23"},
        "dates": {"dates": ["2026-10-23"]},
        "days": {
            "2026-10-23": {
                "date": "2026-10-23",
                "rows": [_row("600519", "2026-10-23", 110.0)],
            }
        },
    }
    pull.pull(path, "https://w", "t", max_days=60, fetch_json=_fetcher(payloads))
    pull.pull(path, "https://w", "t", max_days=60, fetch_json=_fetcher(payloads))
    n = conn.execute(
        "SELECT COUNT(*) FROM daily_prices WHERE trade_date='2026-10-23'"
    ).fetchone()[0]
    assert n == 1


def test_pull_falls_back_when_worker_empty(db):
    path, _ = db
    fetch_json = _fetcher(
        {
            "health": {"latest_date": None},
            "dates": {"dates": []},
            "days": {},
        }
    )
    result = pull.pull(path, "https://w", "t", max_days=60, fetch_json=fetch_json)
    assert result["fell_back"] is True
    assert result["written"] == 0


def test_pull_falls_back_on_http_error(db):
    path, _ = db

    def boom(url, token):
        raise pull.WorkerError("HTTP 500")

    result = pull.pull(path, "https://w", "t", max_days=60, fetch_json=boom)
    assert result["fell_back"] is True


def test_pull_computes_pct_change_when_missing(db):
    """腾讯回退路径 pct_change=0，GH 侧用前一交易日收盘补算。"""
    path, conn = db
    fetch_json = _fetcher(
        {
            "health": {"latest_date": "2026-10-23"},
            "dates": {"dates": ["2026-10-23"]},
            "days": {
                "2026-10-23": {
                    "date": "2026-10-23",
                    "rows": [_row("600519", "2026-10-23", 110.0, 0.0)],
                }
            },
        }
    )
    pull.pull(path, "https://w", "t", max_days=60, fetch_json=fetch_json)
    pct = conn.execute(
        "SELECT pct_change FROM daily_prices WHERE code='600519' AND trade_date='2026-10-23'"
    ).fetchone()[0]
    # 前一日收盘 100 → (110-100)/100*100 = 10.0
    assert abs(pct - 10.0) < 1e-6


def test_pull_without_token_uses_public_read(db):
    """软鉴权：不传 token 时也能拉（Worker 未配 token 的场景）。"""
    path, _ = db
    payloads = {
        "health": {"latest_date": "2026-10-23"},
        "dates": {"dates": ["2026-10-23"]},
        "days": {"2026-10-23": {"date": "2026-10-23",
                                 "rows": [_row("600519", "2026-10-23", 110.0)]}},
    }
    fetch_json = _fetcher(payloads)
    result = pull.pull(path, "https://w", "", max_days=60, fetch_json=fetch_json)
    assert result["fell_back"] is False
    assert result["written"] == 1


def test_cli_without_token_is_not_hard_failure(db, monkeypatch):
    path, _ = db
    monkeypatch.delenv("D1_SYNC_TOKEN", raising=False)
    # 网络不可用 → WorkerError → fell_back → 退出码 3（不是 2）
    assert pull.main(["--db", path, "--worker", "https://127.0.0.1:9", "--token", ""]) == 3


def test_http_json_sends_user_agent(monkeypatch):
    """Cloudflare 会 403 掉默认 Python-urllib UA，必须自带。"""
    captured = {}

    class FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"ok": true}'

    def fake_urlopen(req, timeout=30):
        captured["ua"] = req.get_header("User-agent")
        captured["token"] = req.get_header("X-sync-token")
        return FakeResp()

    monkeypatch.setattr(pull.urllib.request, "urlopen", fake_urlopen)
    assert pull._http_json("https://w/api/market/health", "") == {"ok": True}
    assert captured["ua"].startswith("a-finder-ci/")
    assert captured["token"] is None

    pull._http_json("https://w/api/market/health", "tok")
    assert captured["token"] == "tok"
