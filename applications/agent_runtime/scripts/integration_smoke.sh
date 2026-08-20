#!/usr/bin/env bash
# agent-runtime 集成冒烟测试（M6 用例固化）：真 Redis + MySQL + K8s 全链路。
#
# 用法（参数透传给 e2e_hld_acceptance.py，见 --help）：
#   ./scripts/integration_smoke.sh
#   ./scripts/integration_smoke.sh --base-url http://10.x.x.x:8091/api/session \
#       --redis-url redis://10.x.x.x:6379/1 --namespace agent-runtime-e2e
#
# 前置：服务已以 server 模式运行；kubectl 已配置；Redis DB 建议独立编号
# （脚本会 FLUSHDB，检测到外来 key 会中止，除非 --force-flush）。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${APP_DIR}"
exec uv run --no-sync python scripts/e2e_hld_acceptance.py "$@"
