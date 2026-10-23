#!/usr/bin/env bash
# ============================================================
#   Khởi động Javis OS chạy NỀN - Linux / Ubuntu
# ============================================================
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_DIR"

if [ -f .env ]; then
  _env() { sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*//p" .env | tail -1; }
  : "${JAVIS_PORT:=$(_env JAVIS_PORT)}"
  : "${JAVIS_HOST:=$(_env JAVIS_HOST)}"
  : "${JAVIS_NAME:=$(_env JAVIS_NAME)}"
  : "${JAVIS_STATE_DIR:=$(_env JAVIS_STATE_DIR)}"
fi

PORT="${JAVIS_PORT:-7777}"
HOST="${JAVIS_HOST:-127.0.0.1}"
NAME="${JAVIS_NAME:-javis}"
STATE_DIR="${JAVIS_STATE_DIR:-$APP_DIR/server}"
PIDF="$STATE_DIR/javis.pid"
LOG="$STATE_DIR/javis.log"
PY="$APP_DIR/.venv/bin/python"

# Tắt instance cũ trước nếu đang chạy
if [ -f "$APP_DIR/stop-javis.sh" ]; then
  bash "$APP_DIR/stop-javis.sh" >/dev/null 2>&1 || true
fi

mkdir -p "$STATE_DIR"

if command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files 2>/dev/null | grep -q "^$NAME\.service"; then
  SUDO=""
  [ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null 2>&1 && SUDO="sudo"
  $SUDO systemctl start "$NAME"
else
  if [ ! -x "$PY" ]; then
    echo "Loi: Khong tim thay Python venv tai $PY"
    echo "Hay chay ./install.sh trong thu muc du an truoc."
    exit 1
  fi

  (
    cd "$APP_DIR/server"
    export JAVIS_STATE_DIR="$STATE_DIR"
    export JAVIS_PORT="$PORT"
    export JAVIS_HOST="$HOST"
    nohup "$PY" -m uvicorn main:app --host "$HOST" --port "$PORT" > "$LOG" 2>&1 &
    echo $! > "$PIDF"
  )
fi

echo "Da bat Javis OS chay nen (port $PORT)."
