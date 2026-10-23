#!/usr/bin/env bash
# ============================================================
#   Dừng Javis OS (Stop) - Linux / Ubuntu
# ============================================================
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_DIR"

if [ -f .env ]; then
  _env() { sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*//p" .env | tail -1; }
  : "${JAVIS_PORT:=$(_env JAVIS_PORT)}"
  : "${JAVIS_NAME:=$(_env JAVIS_NAME)}"
  : "${JAVIS_STATE_DIR:=$(_env JAVIS_STATE_DIR)}"
fi

PORT="${JAVIS_PORT:-7777}"
NAME="${JAVIS_NAME:-javis}"
STATE_DIR="${JAVIS_STATE_DIR:-$APP_DIR/server}"
PIDF="$STATE_DIR/javis.pid"

echo "Dang tim va tat Javis OS..."

# 1. Neu chay duoi systemd service
if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet "$NAME" 2>/dev/null; then
  SUDO=""
  [ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null 2>&1 && SUDO="sudo"
  $SUDO systemctl stop "$NAME" 2>/dev/null || true
fi

# 2. Neu chay duoi Docker container
if command -v docker >/dev/null 2>&1 && [ -f docker-compose.yml ] && \
   docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$NAME"; then
  docker compose stop "$NAME" 2>/dev/null || docker stop "$NAME" 2>/dev/null || true
fi

# 3. Tat theo file PID neu ton tai
if [ -f "$PIDF" ]; then
  OLD_PID="$(cat "$PIDF" 2>/dev/null || true)"
  if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
    kill "$OLD_PID" 2>/dev/null || true
  fi
  rm -f "$PIDF"
fi

# 4. Giai phong bat ky tien trinh nao dang chiem cong PORT
PIDS=""
if command -v lsof >/dev/null 2>&1; then
  PIDS="$(lsof -ti tcp:"$PORT" 2>/dev/null || true)"
elif command -v fuser >/dev/null 2>&1; then
  PIDS="$(fuser "$PORT/tcp" 2>/dev/null || true)"
fi

if [ -n "$PIDS" ]; then
  kill $PIDS 2>/dev/null || true
  for _ in {1..5}; do
    sleep 0.5
    PIDS_REMAINING=""
    if command -v lsof >/dev/null 2>&1; then
      PIDS_REMAINING="$(lsof -ti tcp:"$PORT" 2>/dev/null || true)"
    fi
    [ -z "$PIDS_REMAINING" ] && break
  done
  if [ -n "${PIDS_REMAINING:-}" ]; then
    kill -9 $PIDS_REMAINING 2>/dev/null || true
  fi
fi

# 5. Don dep tien trinh uvicorn main:app lien quan neu con sot
pkill -f "$APP_DIR/.*uvicorn.*main:app.*$PORT" 2>/dev/null || true

echo "Xong."
exit 0
