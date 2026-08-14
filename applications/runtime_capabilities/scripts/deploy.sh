#!/usr/bin/env bash
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
    TEMPLATE_FILE="${APP_DIR}/runtime_capabilities.local.env.example"
else
    DEFAULT_ENV_FILE="${APP_DIR}/.env.production.local"
    TEMPLATE_FILE="${APP_DIR}/runtime_capabilities.server.env.example"
fi

ENV_FILE="${2:-${DEFAULT_ENV_FILE}}"
if [[ ! -f "${ENV_FILE}" ]]; then
    if [[ "${MODE}" == "local" && "${ENV_FILE}" == "${DEFAULT_ENV_FILE}" ]]; then
        cp "${TEMPLATE_FILE}" "${ENV_FILE}"
        echo "Created local configuration: ${ENV_FILE}"
    else
        echo "Environment file does not exist: ${ENV_FILE}" >&2
        echo "Copy and edit the template first: ${TEMPLATE_FILE}" >&2
        exit 2
    fi
fi

uv sync --project "${APP_DIR}" --extra "${MODE}"
cd "${APP_DIR}"
exec uv run --project "${APP_DIR}" --no-sync runtime-capabilities \
    --mode "${MODE}" \
    --env-file "${ENV_FILE}"
