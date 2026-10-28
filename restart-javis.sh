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
  : "${JAVIS_STATE_DIR:=$(_env JAVIS_STATE_DIR)}"
fi

PORT="${JAVIS_PORT:-7777}"
HOST="${JAVIS_HOST:-127.0.0.1}"
STATE_DIR="${JAVIS_STATE_DIR:-$APP_DIR/server}"
PIDF="$STATE_DIR/javis.pid"
URL="http://localhost:${PORT}"

_is_port_open() {
  if command -v lsof >/dev/null 2>&1; then
    lsof -i tcp:"$PORT" -sTCP:LISTEN >/dev/null 2>&1
  elif command -v fuser >/dev/null 2>&1; then
    fuser "$PORT/tcp" >/dev/null 2>&1
  elif command -v ss >/dev/null 2>&1; then
    ss -tlH "sport = :$PORT" 2>/dev/null | grep -q .
  else
    (echo > /dev/tcp/127.0.0.1/"$PORT") >/dev/null 2>&1
  fi
}

_get_pids() {
  local pids=""
  if [ -f "$PIDF" ]; then
    local fpid
    fpid="$(cat "$PIDF" 2>/dev/null || true)"
    if [ -n "$fpid" ] && kill -0 "$fpid" 2>/dev/null; then
      pids="$pids $fpid"
    fi
  fi
  if command -v lsof >/dev/null 2>&1; then
    local lpids
    lpids="$(lsof -ti tcp:"$PORT" 2>/dev/null || true)"
    [ -n "$lpids" ] && pids="$pids $lpids"
  elif command -v fuser >/dev/null 2>&1; then
    local fpids
    fpids="$(fuser "$PORT/tcp" 2>/dev/null || true)"
    [ -n "$fpids" ] && pids="$pids $fpids"
  elif command -v ss >/dev/null 2>&1; then
    local spids
    spids="$(ss -tlpn "sport = :$PORT" 2>/dev/null | grep -o 'pid=[0-9]*' | cut -d= -f2 || true)"
    [ -n "$spids" ] && pids="$pids $spids"
  fi
  if command -v pgrep >/dev/null 2>&1; then
    local ppids
    ppids="$(pgrep -f "$APP_DIR/.*uvicorn.*main:app.*$PORT" 2>/dev/null || true)"
    [ -n "$ppids" ] && pids="$pids $ppids"
  fi
  if [ -n "$pids" ]; then
    echo "$pids" | tr ' ' '\n' | grep -E '^[0-9]+$' | sort -u || true
  fi
}

_confirm_stopped() {
  local stopped=false
  for _ in {1..10}; do
    local cur_pids
    cur_pids="$(_get_pids)"
    if [ -z "$cur_pids" ] && ! _is_port_open; then
      stopped=true
      break
    fi
    sleep 0.5
  done

  if [ "$stopped" != "true" ]; then
    local cur_pids
    cur_pids="$(_get_pids)"
    if [ -n "$cur_pids" ]; then
      kill -9 $cur_pids 2>/dev/null || true
    fi
    pkill -9 -f "$APP_DIR/.*uvicorn.*main:app.*$PORT" 2>/dev/null || true

    for _ in {1..10}; do
      cur_pids="$(_get_pids)"
      if [ -z "$cur_pids" ] && ! _is_port_open; then
        stopped=true
        break
      fi
      sleep 0.5
    done
  fi

  local final_pids
  final_pids="$(_get_pids)"
  if [ -n "$final_pids" ] || _is_port_open; then
    echo ""
    echo "============================================================"
    echo "  [LOI] Khong the tat hoan toan instance Javis OS cu:"
    [ -n "$final_pids" ] && echo "  - PID van con chay: $final_pids"
    if _is_port_open; then
      echo "  - Port $PORT van chua closed (dang duoc su dung)."
    fi
    echo "============================================================"
    return 1
  fi

  rm -f "$PIDF"
  return 0
}

echo "[1/3] Dang tat instance Javis OS cu..."
if [ -f "$APP_DIR/stop-javis.sh" ]; then
  bash "$APP_DIR/stop-javis.sh"
else
  TARGET_PIDS="$(_get_pids)"
  if [ -n "$TARGET_PIDS" ]; then
    kill $TARGET_PIDS 2>/dev/null || true
  fi
  pkill -f "$APP_DIR/.*uvicorn.*main:app.*$PORT" 2>/dev/null || true
  if _confirm_stopped; then
    echo "Xong (PID da dong, port $PORT da closed)."
  else
    exit 1
  fi
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
