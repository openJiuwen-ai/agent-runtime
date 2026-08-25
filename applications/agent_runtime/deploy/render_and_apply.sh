#!/usr/bin/env bash
# agent-runtime K8s 部署：渲染 <<VAR>> 模板 → kubectl apply。
#
# 用法：
#   ./render_and_apply.sh [env-file] [--render-only] [--nodeport]
#   ./render_and_apply.sh deploy/agent_runtime.env --nodeport
#
# - env-file：KEY=VALUE + # 注释（默认 deploy/agent_runtime.env）
# - --render-only：只渲染到 deploy/rendered/ 不 apply
# - --nodeport：附带应用 NodePort Service（默认 30091，供集群外访问）
# - 渲染后残留 << 视为漏配变量，fail-fast 列出
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
DEPLOY_DIR="${SCRIPT_DIR}"

ENV_FILE=""
RENDER_ONLY=0
NODEPORT=0
for arg in "$@"; do
    case "${arg}" in
        --render-only) RENDER_ONLY=1 ;;
        --nodeport) NODEPORT=1 ;;
        *) ENV_FILE="${arg}" ;;
    esac
done
ENV_FILE="${ENV_FILE:-${DEPLOY_DIR}/agent_runtime.env}"

if [[ ! -f "${ENV_FILE}" ]]; then
    echo "Environment file does not exist: ${ENV_FILE}" >&2
    echo "Copy deploy/agent_runtime.env.example to it and edit first." >&2
    exit 2
fi

# source 渲染变量（仅大写下划线 KEY=VALUE 行有效；example 自带注释）
set -a
# shellcheck disable=SC1090
source <(grep -E '^[A-Z_][A-Z0-9_]*=' "${ENV_FILE}")
set +a

RENDER_DIR="${DEPLOY_DIR}/rendered"
mkdir -p "${RENDER_DIR}"

render() {
    local src="$1" dst="$2"
    local tmp="${dst}.tmp"
    cp "${src}" "${tmp}"
    # 防变量值里带 / 干扰 sed 分隔符：用 | 做分隔
    local name value
    while IFS='=' read -r name value; do
        [[ -z "${name}" ]] && continue
        value="${value%\"}" ; value="${value#\"}"
        sed -i "s|<<${name}>>|${value}|g" "${tmp}"
    done < <(grep -E '^[A-Z_][A-Z0-9_]*=' "${ENV_FILE}")
    if grep -q '<<' "${tmp}"; then
        echo "ERROR: ${dst} 渲染后仍有未替换占位符（env 缺变量）：" >&2
        grep -o '<<[A-Z_0-9]*>>' "${tmp}" | sort -u >&2
        rm -f "${tmp}"
        exit 1
    fi
    mv "${tmp}" "${dst}"
    echo "[render] ${dst}"
}

render "${DEPLOY_DIR}/agent_runtime.template.yaml" \
       "${RENDER_DIR}/agent_runtime.yaml"
if [[ ${NODEPORT} -eq 1 ]]; then
    render "${DEPLOY_DIR}/agent_runtime_nodeport.template.yaml" \
           "${RENDER_DIR}/agent_runtime_nodeport.yaml"
fi

if [[ ${RENDER_ONLY} -eq 1 ]]; then
    echo "[render-only] done: ${RENDER_DIR}/"
    exit 0
fi

command -v kubectl >/dev/null || { echo "kubectl not found" >&2; exit 2; }

kubectl apply -f "${RENDER_DIR}/agent_runtime.yaml"
if [[ ${NODEPORT} -eq 1 ]]; then
    kubectl apply -f "${RENDER_DIR}/agent_runtime_nodeport.yaml"
fi

echo "[apply] waiting rollout: deployment/${AGENT_RUNTIME_NAME}"
kubectl rollout status "deployment/${AGENT_RUNTIME_NAME}" \
    -n "${NAMESPACE:-default}"
kubectl get pods -n "${NAMESPACE:-default}" -l "app=${AGENT_RUNTIME_NAME}" -o wide
echo "[apply] done. 集群内入口: ${AGENT_RUNTIME_NAME}.${NAMESPACE:-default}:8091"
[[ ${NODEPORT} -eq 1 ]] && echo "[apply] 集群外入口: http://<节点IP>:${AGENT_RUNTIME_NODE_PORT}/api/session"
