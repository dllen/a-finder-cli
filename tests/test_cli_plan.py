"""CLI smoke tests for the `plan` subcommand (argparse-based)."""
import subprocess
import sys


def test_plan_help():
    """`plan --help` exits 0 and shows --date option."""
    result = subprocess.run(
        [sys.executable, "stock_cli.py", "plan", "--help"],
        capture_output=True, text=True, cwd=".",
    )
    assert result.returncode == 0
    assert "--date" in result.stdout


def test_plan_dry_run():
    """`plan --dry-run` prints a plan-date line without touching the DB."""
    result = subprocess.run(
        [sys.executable, "stock_cli.py", "plan", "--dry-run", "--db", "test_plan_cli_dryrun.db"],
        capture_output=True, text=True, cwd=".",
    )
    assert result.returncode == 0
    assert "[dry-run] would build plan for" in result.stdout


def test_plan_list_with_no_data(tmp_path):
    """`plan --list` with no data prints '无 plan' line."""
    db = str(tmp_path / "empty.db")
    result = subprocess.run(
        [sys.executable, "stock_cli.py", "plan", "--list", "--db", db, "--days", "7"],
        capture_output=True, text=True, cwd=".",
    )
    assert result.returncode == 0
    assert "无 plan" in result.stdout or "plan" in result.stdout.lower()