#!/usr/bin/env bash
# ============================================================
#   Khởi động lại Javis OS (Restart) - Linux / Ubuntu
# ============================================================
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_DIR"

echo "============================================================"
echo "  Khoi dong lai Javis OS (Restart)"
echo "============================================================"
echo ""

# Đọc cấu hình từ .env nếu có
if [ -f .env ]; then
  _env() { sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*//p" .env | tail -1; }
  : "${JAVIS_PORT:=$(_env JAVIS_PORT)}"
  : "${JAVIS_HOST:=$(_env JAVIS_HOST)}"
fi

PORT="${JAVIS_PORT:-7777}"
HOST="${JAVIS_HOST:-127.0.0.1}"
URL="http://localhost:${PORT}"

echo "[1/3] Dang tat instance Javis OS cu..."
if [ -f "$APP_DIR/stop-javis.sh" ]; then
  bash "$APP_DIR/stop-javis.sh"
else
  PIDS=""
  if command -v lsof >/dev/null 2>&1; then
    PIDS="$(lsof -ti tcp:"$PORT" 2>/dev/null || true)"
  elif command -v fuser >/dev/null 2>&1; then
    PIDS="$(fuser "$PORT/tcp" 2>/dev/null || true)"
  fi
  if [ -n "$PIDS" ]; then
    kill $PIDS 2>/dev/null || true
    sleep 2
  fi
  pkill -f "$APP_DIR/.*uvicorn.*main:app.*$PORT" 2>/dev/null || true
  echo "Xong."
fi

echo ""
echo "[2/3] Dang bat lai Javis OS chay NEN (port $PORT)..."
if [ -f "$APP_DIR/start-javis.sh" ]; then
  bash "$APP_DIR/start-javis.sh"
else
  mkdir -p "$APP_DIR/server"
  PY="$APP_DIR/.venv/bin/python"
  (
    cd "$APP_DIR/server"
    export JAVIS_STATE_DIR="$APP_DIR/server"
    export JAVIS_PORT="$PORT"
    export JAVIS_HOST="$HOST"
    nohup "$PY" -m uvicorn main:app --host "$HOST" --port "$PORT" > "$APP_DIR/server/javis.log" 2>&1 &
    echo $! > "$APP_DIR/server/javis.pid"
  )
fi

echo ""
echo "[3/3] Dang kiem tra ket noi den $URL..."

if ! command -v curl >/dev/null 2>&1; then
  echo "Khong tim thay curl. Cho 10 giay de server khoi dong..."
  sleep 10
  echo ""
  echo "============================================================"
  echo "  [OK] Javis OS da san sang tai $URL!"
  echo "  Hay mo $URL va bam Ctrl+Shift+R de lam moi."
  echo "============================================================"
  exit 0
fi

_count=0
_max=45

while true; do
  _count=$(( _count + 1 ))
  if curl -sf -m 2 -o /dev/null "$URL/health" 2>/dev/null; then
    echo ""
    echo "============================================================"
    echo "  [OK] Javis OS da san sang tai $URL!"
    echo "  Hay mo $URL va bam Ctrl+Shift+R de lam moi."
    echo "============================================================"
    echo ""
    exit 0
  fi

  if [ "$_count" -ge "$_max" ]; then
    echo ""
    echo "============================================================"
    echo "  [CANH BAO] Qua thoi gian cho (${_max}s), chua the ket noi $URL."
    echo "  Server co the dang khoi dong cham hoac gap su co."
    echo "  Kiem tra loi tai file: server/javis.log"
    echo "============================================================"
    echo ""
    exit 1
  fi

  printf "."
  sleep 1
done
