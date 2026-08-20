#!/usr/bin/env bash
# agent-runtime 会话编排服务部署脚本（模式参考 runtime_capabilities/scripts/deploy.sh）。
#
# 用法：
#   ./deploy.sh local   [env-file]   # 开发/测试：fakeredis + fake K8s + SQLite
#   ./deploy.sh server  [env-file]   # 部署：真 Redis + MySQL + K8s（默认读 .env.production.local）
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
MODE="${1:-}"

if [[ "${MODE}" != "local" && "${MODE}" != "server" ]]; then
    echo "Usage: $0 <local|server> [env-file]" >&2
    exit 2
fi

if [[ "${MODE}" == "local" ]]; then
    DEFAULT_ENV_FILE="${APP_DIR}/.env.development.local"
else
    DEFAULT_ENV_FILE="${APP_DIR}/.env.production.local"
fi

ENV_FILE="${2:-${DEFAULT_ENV_FILE}}"
if [[ ! -f "${ENV_FILE}" ]]; then
    echo "Environment file does not exist: ${ENV_FILE}" >&2
    echo "Copy agent_runtime.${MODE}.env.example to it and edit first." >&2
    exit 2
fi

uv sync --project "${APP_DIR}" --extra "${MODE}"
cd "${APP_DIR}"
exec uv run --project "${APP_DIR}" --no-sync agent-runtime \
    --mode "${MODE}" \
    --env-file "${ENV_FILE}"
