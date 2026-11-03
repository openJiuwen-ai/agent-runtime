#!/usr/bin/env bash
# Local-only Manager stack. Run from any directory; Ctrl-C stops all children.
set -euo pipefail
case "${1:-}" in
  --offline) export MANAGER_DEV_OFFLINE=1 ;;
  --live) export MANAGER_DEV_OFFLINE=0 ;;
  '') export MANAGER_DEV_OFFLINE="${MANAGER_DEV_OFFLINE:-0}" ;;
  *) echo 'Usage: local.sh [--offline|--live]' >&2; exit 2 ;;
esac
export MANAGER_LOCAL_DEV=1
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
STATE="$ROOT/.manager-dev"
PYTHON="$ROOT/.venv-manager/bin/python"
mkdir -p "$STATE"
if [[ ! -x "$PYTHON" ]]; then
  echo 'Install .venv-manager dependencies first; see dev/README.md.' >&2
  exit 1
fi
export IDENTITY_ADMIN_PASSWORD=admin IDENTITY_USER1_PASSWORD=user
export IDENTITY_REST_HOST=127.0.0.1 IDENTITY_REST_PORT=18770
export MANAGER_REST_HOST=127.0.0.1 MANAGER_REST_PORT=18765
export IDENTITY_DB_TYPE=sqlite MANAGER_DB_TYPE=sqlite
export IDENTITY_SQLITE_PATH="$STATE/identity.db" MANAGER_SQLITE_PATH="$STATE/manager.db"
export IDENTITY_PUBLIC_KEY_URL=http://127.0.0.1:18770/v1/auth/public_key
export MANAGER_WEB_IDP_TARGET=http://127.0.0.1:18770
export MANAGER_WEB_PROXY_TARGET=http://127.0.0.1:18765
pids=()
cleanup() {
  for pid in ${pids[@]+"${pids[@]}"}; do kill "$pid" 2>/dev/null || true; done
  for pid in ${pids[@]+"${pids[@]}"}; do wait "$pid" 2>/dev/null || true; done
}
trap cleanup EXIT
trap 'exit 130' INT TERM
# Fail early instead of accidentally using a pre-existing service.
"$PYTHON" - <<'PY'
import socket
for port in (18770, 18765, 5273):
    with socket.socket() as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(('127.0.0.1', port))
PY
cd "$ROOT"
"$PYTHON" "$ROOT/applications/manager/dev/accounts.py"
"$PYTHON" -m uvicorn identity_center.app:app --host 127.0.0.1 --port 18770 --reload --reload-dir applications/manager/identity_center/src --reload-dir service --reload-dir foundation > "$STATE/identity.log" 2>&1 &
pids+=("$!")
"$PYTHON" -m uvicorn manager_server.app:app --host 127.0.0.1 --port 18765 --reload --reload-dir applications/manager/manager_server/src --reload-dir service --reload-dir foundation > "$STATE/manager.log" 2>&1 &
pids+=("$!")
cd "$ROOT/applications/manager/manager_web"
./node_modules/.bin/vite --host 127.0.0.1 > "$STATE/web.log" 2>&1 &
pids+=("$!")
echo "Manager: http://127.0.0.1:5273/manager"
echo "Local credentials: admin/admin and user/user"
echo "Logs: $STATE/{identity,manager,web}.log"
echo "Offline UI development: $MANAGER_DEV_OFFLINE (use --live to restore real probes and config delivery)"
wait
