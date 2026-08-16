#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
DB="${DB:-hs300.db}"
PORT="${PORT:-8000}"
TOP="${TOP:-10}"
PID_FILE="$ROOT_DIR/.web.pid"
LOG_FILE="$ROOT_DIR/web.log"

if command -v uv >/dev/null 2>&1 && [[ -d "$ROOT_DIR/.venv" ]]; then
  PY="$ROOT_DIR/.venv/bin/python"
else
  PY="python3"
fi

port_pid() {
  lsof -ti ":$PORT" 2>/dev/null || true
}

is_running() {
  [[ -n "$(port_pid)" ]]
}

start() {
  if is_running; then
    echo "already running (pid $(port_pid)) http://127.0.0.1:$PORT"
    exit 0
  fi
  nohup "$PY" "$ROOT_DIR/app.py" --db "$DB" --port "$PORT" --top "$TOP" >>"$LOG_FILE" 2>&1 &
  echo $! >"$PID_FILE"
  echo "started http://127.0.0.1:$PORT (pid $(cat "$PID_FILE"))"
}

stop() {
  local pid
  pid="$(port_pid)"
  if [[ -n "$pid" ]]; then
    kill $pid
    echo "stopped"
  else
    echo "not running"
  fi
  rm -f "$PID_FILE"
}

cmd="${1:-start}"
case "$cmd" in
  start)   start ;;
  stop)    stop ;;
  restart) stop; start ;;
  status)  is_running && echo "running (pid $(port_pid)) http://127.0.0.1:$PORT" || echo "stopped" ;;
  *)       echo "usage: $0 {start|stop|restart|status}"; exit 1 ;;
esac
