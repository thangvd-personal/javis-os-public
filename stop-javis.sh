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
fi

# 4. Giai phong bat ky tien trinh nao dang chiem cong PORT
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

PIDS="$(_get_pids)"
if [ -n "$PIDS" ]; then
  kill $PIDS 2>/dev/null || true
fi

# 5. Don dep tien trinh uvicorn main:app lien quan neu con sot
pkill -f "$APP_DIR/.*uvicorn.*main:app.*$PORT" 2>/dev/null || true

# 6. Cho va xac nhan tat ca PID da dong that su, port da closed
_stopped=false
for _ in {1..10}; do
  CURRENT_PIDS="$(_get_pids)"
  if [ -z "$CURRENT_PIDS" ] && ! _is_port_open; then
    _stopped=true
    break
  fi
  sleep 0.5
done

if [ "$_stopped" != "true" ]; then
  CURRENT_PIDS="$(_get_pids)"
  if [ -n "$CURRENT_PIDS" ]; then
    kill -9 $CURRENT_PIDS 2>/dev/null || true
  fi
  pkill -9 -f "$APP_DIR/.*uvicorn.*main:app.*$PORT" 2>/dev/null || true

  for _ in {1..10}; do
    CURRENT_PIDS="$(_get_pids)"
    if [ -z "$CURRENT_PIDS" ] && ! _is_port_open; then
      _stopped=true
      break
    fi
    sleep 0.5
  done
fi

FINAL_PIDS="$(_get_pids)"
if [ -n "$FINAL_PIDS" ] || _is_port_open; then
  echo ""
  echo "============================================================"
  echo "  [LOI] Khong the tat hoan toan Javis OS:"
  [ -n "$FINAL_PIDS" ] && echo "  - PID van con chay: $FINAL_PIDS"
  if _is_port_open; then
    echo "  - Port $PORT van chua closed (dang mo)."
  fi
  echo "============================================================"
  exit 1
fi

rm -f "$PIDF"
echo "Xong (PID da dong, port $PORT da closed)."
exit 0
