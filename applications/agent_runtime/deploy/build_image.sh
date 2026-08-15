#!/usr/bin/env bash
# agent-runtime 镜像构建/推送。
#
# 用法：
#   ./build_image.sh [tag] [--push]
#   ./build_image.sh swr.cn-north-4.myhuaweicloud.com/openjiuwen/jiuwenclaw-agent-runtime-amd64:0.0.1 --push
#
# 构建上下文必须是仓库根（Dockerfile 内 COPY foundation/ service/
# applications/agent_runtime/，保持 [tool.uv.sources] 的相对布局）。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${APP_DIR}/../.." && pwd)"

DEFAULT_TAG="agent-runtime:dev"
TAG="${DEFAULT_TAG}"
PUSH=0
for arg in "$@"; do
    case "${arg}" in
        --push) PUSH=1 ;;
        *) TAG="${arg}" ;;
    esac
done

command -v docker >/dev/null || { echo "docker not found" >&2; exit 2; }

echo "[build] context=${REPO_ROOT} tag=${TAG}"
docker build -f "${SCRIPT_DIR}/Dockerfile" -t "${TAG}" "${REPO_ROOT}"

if [[ ${PUSH} -eq 1 ]]; then
    echo "[push] ${TAG}"
    docker push "${TAG}"
fi
echo "[build] done: ${TAG}"
