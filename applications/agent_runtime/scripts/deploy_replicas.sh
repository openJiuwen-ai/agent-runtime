#!/usr/bin/env bash
# agent-runtime 宿主机多副本启动（快速多实例测试用，非生产形态）。
#
# 同机起 N 个 server 模式进程（连续端口），共享同一 Redis/DB/K8s（来自
# env-file）—— 等价「多副本 + 客户端轮询」的最小多实例环境：
#   ./scripts/deploy_replicas.sh 2 .env.production.local 8091
#   → http://127.0.0.1:8091/api/session 与 :8092 并存，选主键互斥竞争。
#
# 生产多副本走 deploy/（K8s Deployment + Service LB）。
#
# 注意：
# - AGENT_RUNTIME_MODE=local 的进程内 fakeredis/FakeK8s 无法跨进程共享，
#   local 模式直接 fail-fast（红线：不静默降级）。
# - Ctrl-C / TERM 退出时 trap 清理全部子进程。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

N="${1:-}"
ENV_FILE="${2:-${APP_DIR}/.env.production.local}"
START_PORT="${3:-8091}"
READY_TIMEOUT=60

usage() {
    echo "Usage: $0 <N> [env-file] [start-port]" >&2
    echo "  N           副本数（≥1）" >&2
    echo "  env-file    环境文件（默认 .env.production.local）" >&2
    echo "  start-port  起始端口（默认 8091，第 i 个副本用 start+i）" >&2
    exit 2
}

[[ "${N}" =~ ^[1-9][0-9]*$ ]] || usage
if [[ ! -f "${ENV_FILE}" ]]; then
    echo "Environment file does not exist: ${ENV_FILE}" >&2
    exit 2
fi
if grep -qE '^AGENT_RUNTIME_MODE=local' "${ENV_FILE}"; then
    echo "ERROR: ${ENV_FILE} 是 AGENT_RUNTIME_MODE=local —— 进程内 fakeredis/FakeK8s" >&2
    echo "       无法跨进程共享，local 模式不支持多副本（不静默降级）。" >&2
    exit 2
fi

PIDS=()
REPLICA_LOGS=()

cleanup() {
    [[ ${#PIDS[@]} -eq 0 ]] && return
    echo "[deploy_replicas] stopping ${#PIDS[@]} replica(s)..."
    for pid in "${PIDS[@]}"; do
        kill "${pid}" 2>/dev/null || true
    done
    wait 2>/dev/null || true
    PIDS=()
}
trap cleanup INT TERM EXIT

mkdir -p "${APP_DIR}/logs"
# 双 extra 一起装：server 依赖齐 + local/dev（fakeredis 等）不被卸掉，
# 免得切一次 extra 就破坏 pytest 环境
uv sync --project "${APP_DIR}" --extra server --extra local --quiet

echo "[deploy_replicas] starting ${N} replica(s) from port ${START_PORT} (env: ${ENV_FILE})"

BASE_URLS=()
for i in $(seq 0 $((N - 1))); do
    port=$((START_PORT + i))
    log="${APP_DIR}/logs/replica-${port}.log"
    REPLICA_LOGS+=("${log}")
    : > "${log}"
    (cd "${APP_DIR}" && exec uv run --project "${APP_DIR}" --no-sync agent-runtime \
        --mode server --env-file "${ENV_FILE}" --port "${port}") >> "${log}" 2>&1 &
    PIDS+=($!)
    BASE_URLS+=("http://127.0.0.1:${port}/api/session")
    echo "[deploy_replicas] replica-${i} pid=${PIDS[-1]} port=${port} log=${log}"
done

# 就绪轮询：/healthz 200（lifespan 完成即 sysctx 就绪；起不来 fail-fast）
for i in $(seq 0 $((N - 1))); do
    port=$((START_PORT + i))
    ok=0
    for _ in $(seq 1 $((READY_TIMEOUT * 2))); do
        if curl -sf "http://127.0.0.1:${port}/healthz" >/dev/null 2>&1; then
            ok=1
            break
        fi
        # 进程中途死掉：打印日志尾并失败退出
        if ! kill -0 "${PIDS[$i]}" 2>/dev/null; then
            echo "ERROR: replica-${i} (port ${port}) exited early; log tail:" >&2
            tail -n 30 "${REPLICA_LOGS[$i]}" >&2
            exit 1
        fi
        sleep 0.5
    done
    if [[ ${ok} -ne 1 ]]; then
        echo "ERROR: replica-${i} (port ${port}) not ready in ${READY_TIMEOUT}s; log tail:" >&2
        tail -n 30 "${REPLICA_LOGS[$i]}" >&2
        exit 1
    fi
    echo "[deploy_replicas] replica-${i} (port ${port}) ready: $(curl -sf "http://127.0.0.1:${port}/healthz")"
done

# 输出 base-url 清单（stdout + .replicas.json，供 e2e/压测脚本消费）
MANIFEST="${APP_DIR}/.replicas.json"
printf '%s\n' "${BASE_URLS[@]}" > "${APP_DIR}/.replicas.txt"
python3 - "$MANIFEST" "${BASE_URLS[@]}" <<'PY'
import json, sys
path, urls = sys.argv[1], sys.argv[2:]
with open(path, "w") as f:
    json.dump({"base_urls": urls}, f, indent=2)
PY

echo "[deploy_replicas] all ${N} replica(s) ready. Base URLs:"
printf '  %s\n' "${BASE_URLS[@]}"
echo "[deploy_replicas] manifest: ${MANIFEST} / .replicas.txt"
echo "[deploy_replicas] Ctrl-C 停止全部副本；选主互斥可观察 Redis 键 agent_runtime:job:*:winner:*"

wait
