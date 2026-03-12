#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$ROOT_DIR/.a-finder.pid"
LOG_FILE="$ROOT_DIR/a-finder.log"

run_cmd() {
  if command -v uv >/dev/null 2>&1; then
    uv run a-finder "$@"
  else
    python3 "$ROOT_DIR/stock_cli.py" "$@"
  fi
}

is_running() {
  if [[ -f "$PID_FILE" ]]; then
    local pid
    pid="$(cat "$PID_FILE")"
    if [[ -n "$pid" ]] && kill -0 "$pid" >/dev/null 2>&1; then
      return 0
    fi
  fi
  return 1
}

start_app() {
  if is_running; then
    echo "already running"
    exit 0
  fi
  local args=("$@")
  if [[ ${#args[@]} -eq 0 ]]; then
    args=("overview")
  fi
  run_cmd "${args[@]}" >>"$LOG_FILE" 2>&1 &
  echo $! >"$PID_FILE"
  echo "started"
}

stop_app() {
  if [[ -f "$PID_FILE" ]]; then
    local pid
    pid="$(cat "$PID_FILE")"
    if [[ -n "$pid" ]] && kill -0 "$pid" >/dev/null 2>&1; then
      kill "$pid"
      wait "$pid" >/dev/null 2>&1 || true
      echo "stopped"
    else
      echo "not running"
    fi
    rm -f "$PID_FILE"
  else
    echo "not running"
  fi
}

status_app() {
  if is_running; then
    echo "running"
  else
    echo "stopped"
  fi
}

cmd="${1:-}"
shift || true

case "$cmd" in
  start)
    start_app "$@"
    ;;
  stop)
    stop_app
    ;;
  restart)
    stop_app
    start_app "$@"
    ;;
  status)
    status_app
    ;;
  *)
    echo "usage: $0 {start|stop|restart|status} [args]"
    exit 1
    ;;
esac
