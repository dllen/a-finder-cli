"""迁移的 SQLite 版本门（`-- requires-sqlite:`）—— CI 冷启动崩溃的直接修复。

**事故（2026-11-05，CI 实测）**

`db/migrations/2026_10_24_convertible_bond_constraints.sql` 用了
`ALTER TABLE ... ALTER COLUMN ... SET NOT NULL`（需要 SQLite >= 3.53）。

- 本机 sqlite 3.53.1 → 正常；
- GitHub runner 的 python3.11 链接的 SQLite 更老 → **只在全新库上**抛

      sqlite3.OperationalError: near "ALTER": syntax error

⇒ `open_db()` 崩 ⇒ 同步 3 秒退出 ⇒ **行情一行都抓不到、谱系永远建不起来**。

为什么排查花了很久：本地库的 `_applied_migrations` 早就记过这个文件，
直接跳过 ⇒ **本机永远复现不出来**。必须用「全新空库」才能看到。

修法：迁移文件头部声明 `-- requires-sqlite: 3.53`，
`_run_migrations` 遇到版本不够时**跳过并告警**，而不是让整条流水线崩。
跳过是安全的：该迁移只为**存量库**补 NOT NULL 约束，
全新库在 base 迁移里就带着约束建表（有单测保证）。
"""

from pathlib import Path

import db_repository as dr


MIGRATIONS = Path(dr.__file__).parent / "db" / "migrations"
CONSTRAINTS_SQL = MIGRATIONS / "2026_10_24_convertible_bond_constraints.sql"


class TestDirectiveParsing:

    def test_parses_version(self):
        text = "-- requires-sqlite: 3.53"
        assert dr._required_sqlite(text) == (3, 53)

    def test_three_part_version(self):
        assert dr._required_sqlite("-- requires-sqlite: 3.53.1") == (3, 53, 1)

    def test_no_directive_returns_none(self):
        assert dr._required_sqlite("ALTER TABLE t ADD COLUMN c TEXT") is None

    def test_current_version_is_a_tuple(self):
        version = dr._sqlite_version_tuple()
        assert isinstance(version, tuple) and len(version) >= 2
        assert all(isinstance(p, int) for p in version)


class TestMigrationFileDeclaresRequirement:

    def test_constraints_migration_declares_sqlite_353(self):
        text = CONSTRAINTS_SQL.read_text(encoding="utf-8")
        assert dr._required_sqlite(text) == (3, 53)

    def test_it_really_uses_alter_column(self):
        """确认声明不是凭空的（防止有人删了语句却留着声明）。"""
        text = CONSTRAINTS_SQL.read_text(encoding="utf-8")
        assert "ALTER COLUMN" in text


class TestGateSkipsInsteadOfCrashing:

    def test_old_sqlite_skips_migration_without_raising(self, tmp_path, monkeypatch):
        """模拟老 SQLite：跳过、记账、表仍在、**不抛异常**。"""
        monkeypatch.setattr(dr, "_sqlite_version_tuple", lambda: (3, 45, 0))
        conn = dr.open_db(str(tmp_path / "old.db"))
        try:
            applied = {r[0] for r in conn.execute(
                "SELECT filename FROM _applied_migrations")}
            assert "2026_10_24_convertible_bond_constraints.sql" in applied
            exists = conn.execute(
                "SELECT name FROM sqlite_master WHERE name = 'cb_universe'").fetchone()
            assert exists
        finally:
            conn.close()

    def test_new_sqlite_applies_migration(self, tmp_path, monkeypatch):
        """版本够时照常执行（不能被门误伤）。"""
        monkeypatch.setattr(dr, "_sqlite_version_tuple", lambda: (99, 0, 0))
        conn = dr.open_db(str(tmp_path / "new.db"))
        try:
            cols = {r[1]: r[3] for r in conn.execute("PRAGMA table_info(cb_universe)")}
            assert cols.get("trade_date") == 1
            assert cols.get("code") == 1
        finally:
            conn.close()
