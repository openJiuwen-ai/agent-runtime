#!/usr/bin/env bash
# 向 agent-runtime 发送一次 config_sync(三段式契约:containers/templates/scopes),
# 触发 template + scope 下发。需 agent-runtime 已升级到容器表拆分版本
# (docs/feature/2026-08-container-table-split.md);旧内联载荷过渡期仍被接受。

set -euo pipefail

NAMESPACE="chenhui"
AGENT_SERVER_IMAGE="swr.cn-north-4.myhuaweicloud.com/openjiuwen/jiuwenclaw-agentserver-arm64:0.0.8s"
JIUWENBOX_IMAGE="swr.cn-north-4.myhuaweicloud.com/openjiuwen/jiuwenclaw-sandbox-arm64:0.0.8s"
LABEL="app=jiuwenclaw-agent-runtime"
PORT="8091"
NODE="arm-master"

# 从 .env.custom 读模型/实例配置(可覆盖默认值);缺失时用参考 pod 的值
ENV_CUSTOM="${ENV_CUSTOM:-$(dirname "$0")/.env.custom}"
if [ -f "$ENV_CUSTOM" ]; then
    set -a
    # shellcheck disable=SC1090
    source "$ENV_CUSTOM"
    set +a
    echo "=== 0. 已加载 $ENV_CUSTOM(MODEL_NAME=${MODEL_NAME:-}, JIUWENCLAW_ID=${JIUWENCLAW_ID:-})==="
else
    echo "WARNING: $ENV_CUSTOM 不存在,使用参考 pod 的默认值"
    exit
fi

echo "=== 1. 获取 agent-runtime pod ==="
AGENT_RUNTIME_POD=$(kubectl get pod -n "${NAMESPACE}" -l "${LABEL}" \
         -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
if [ -z "${AGENT_RUNTIME_POD}" ]; then
    echo "ERROR: 在 namespace=${NAMESPACE} 找不到 label=${LABEL} 的 pod"
    echo "       先 kubectl get pod -n ${NAMESPACE} 确认 agent-runtime 在跑"
    exit 1
fi
echo "  pod: ${AGENT_RUNTIME_POD}"

TIME_STAMP=$(date +%s)
echo "  request_id: cfg-${TIME_STAMP}"
echo "=== 2. 发送 config_sync(三段式:容器段 + 模板段(持引用与 volumes)+ scope)==="

# heredoc 顶格(无缩进)—— JSON 不能有前导空格
kubectl exec -n "${NAMESPACE}" "${AGENT_RUNTIME_POD}" -i -- \
    curl -s -X POST "http://127.0.0.1:${PORT}/api/session/config_sync" \
    -H "Content-Type: application/json" \
    -d @- <<EOF
{
  "type": "config_sync",
  "metadata": {
    "request_id": "cfg-${TIME_STAMP}",
    "session_id": null,
    "user_id": "ops",
    "bot_id": "b",
    "extra": {"group_id": "ops"}
  },
  "rawdata": {
    "containers": [
      {
        "container_id": "c-agentserver-main",
        "name": "jiuwenclaw-agentserver",
        "image": "${AGENT_SERVER_IMAGE}",
        "imagePullPolicy": "IfNotPresent",
        "ports": [
          {"name": "sse", "containerPort": 8766},
          {"name": "http", "containerPort": 18092}
        ],
        "env": [
          {"name": "AGENT_SERVER_HOST", "value": "0.0.0.0"},
          {"name": "AGENT_HTTP_ENABLED", "value": "true"},
          {"name": "AGENT_HTTP_HOST", "value": "0.0.0.0"},
          {"name": "AGENT_HTTP_PORT", "value": "8766"},
          {"name": "TZ", "value": "Asia/Shanghai"},
          {"name": "HOME", "value": "/root"},
          {"name": "LOG_ROOT_PATH", "value": "/root/.logs"},
          {"name": "JIUWENSWARM_EDITION", "value": "enterprise"},
          {"name": "MODEL_PROVIDER", "value": "${MODEL_PROVIDER}"},
          {"name": "MODEL_NAME", "value": "${MODEL_NAME}"},
          {"name": "API_BASE", "value": "${API_BASE}"},
          {"name": "API_KEY", "value": "${API_KEY}"},
          {"name": "GATEWAY_DB_TYPE", "value": "mysql"},
          {"name": "GATEWAY_SQLITE_PATH", "value": "gateway.db"},
          {"name": "GATEWAY_DB_HOST", "value": "mysql-headless.default"},
          {"name": "GATEWAY_DB_PORT", "value": "3306"},
          {"name": "GATEWAY_DB_USER", "value": "root"},
          {"name": "GATEWAY_DB_PASSWORD", "value": "Root@123456"},
          {"name": "GATEWAY_DB_NAME", "value": "gateway"},
          {"name": "GATEWAY_PG_SCHEMA", "value": "public"},
          {"name": "RUNTIME_DB_POOL_SIZE", "value": "2"},
          {"name": "RUNTIME_DB_MAX_OVERFLOW", "value": "20"},
          {"name": "RUNTIME_DB_POOL_TIMEOUT", "value": "30"},
          {"name": "LLM_SSL_VERIFY", "value": "False"},
          {"name": "LOG_MASK_ENABLED", "value": "false"},
          {"name": "LOG_TO_FILE_ENABLED", "value": "true"},
          {"name": "NO_PROXY", "value": "localhost,127.0.0.1,api.openai.rnd.huawei.com"},
          {"name": "JIUWENCLAW_SANDBOX_ENABLED", "value": "true"},
          {"name": "JIUWENCLAW_SANDBOX_URL", "value": "http://127.0.0.1:8321"},
          {"name": "JIUWENCLAW_SANDBOX_TYPE", "value": "jiuwenbox"},
          {"name": "JIUWENCLAW_SANDBOX_STARTUP_MODE", "value": "external"},
          {"name": "JIUWENCLAW_SANDBOX_PRESERVE_FILE_SHARING_MODE", "value": "mount"},
          {"name": "JIUWENBOX_FALLBACK_ON_FAILURE", "value": "False"}
        ],
        "securityContext": {"runAsUser": 0, "runAsGroup": 0},
        "readinessProbe": {"httpGet": {"path": "/api/v1/health", "port": 8766},
                            "initialDelaySeconds": 5, "periodSeconds": 10},
        "volumeMounts": [
          {"name": "hp-code", "mountPath": "/app/jiuwenswarm"},
          {"name": "hp-openjiuwen", "mountPath": "/usr/local/lib/python3.11/site-packages/openjiuwen"},
          {"name": "gw-config", "mountPath": "/root/.jiuwenswarm/config/config.yaml", "subPath": "config.yaml"},
          {"name": "gw-envfile", "mountPath": "/root/.jiuwenswarm/config/.env", "subPath": ".env"},
          {"name": "data", "mountPath": "/root/.jiuwenswarm"}
        ]
      },
      {
        "container_id": "c-jiuwenbox",
        "name": "jiuwenbox",
        "image": "${JIUWENBOX_IMAGE}",
        "imagePullPolicy": "IfNotPresent",
        "ports": [{"containerPort": 8321}],
        "env": [
          {"name": "JIUWENBOX_LISTEN", "value": "http://0.0.0.0:8321"},
          {"name": "JIUWENBOX_POLICY_PATH", "value": "/usr/local/lib/python3.11/site-packages/jiuwenbox/configs/enterprise-policy.yaml"},
          {"name": "TZ", "value": "Asia/Shanghai"}
        ],
        "securityContext": {
          "privileged": true,
          "capabilities": {"add": ["SYS_ADMIN", "NET_ADMIN"]},
          "seccompProfile": {"type": "Unconfined"},
          "appArmorProfile": {"type": "Unconfined"}
        },
        "readinessProbe": {"tcpSocket": {"port": 8321},
                            "initialDelaySeconds": 10, "periodSeconds": 5},
        "volumeMounts": [
          {"name": "hp-cgroup", "mountPath": "/sys/fs/cgroup"},
          {"name": "hp-jiuwenbox", "mountPath": "/usr/local/lib/python3.11/site-packages/jiuwenbox"},
          {"name": "data", "mountPath": "/root/.jiuwenswarm"}
        ]
      }
    ],
    "templates": [{
      "template_id": "tpl-fallback",
      "template_name": "default",
      "main_container_id": "c-agentserver-main",
      "sidecar_container_ids": ["c-jiuwenbox"],
      "pod_name": "jiuwenclaw-agentserver",
      "namespace": "${NAMESPACE}",
      "nodeName": "arm-master",
      "sse_path": "/api/v1/events/stream",
      "scope_concurrency": 3,
      "pod_concurrency": 2,
      "session_ttl": 60,
      "pod_ttl": 3600,
      "min_idle_pods": 1,
      "ready_timeout": 240,
      "volumes": [
        {"name": "hp-code", "hostPath": {"path": "${CLAW_CODE_PATH}", "type": "Directory"}},
        {"name": "hp-openjiuwen", "hostPath": {"path": "${CORE_CODE_PATH}/openjiuwen", "type": "Directory"}},
        {"name": "gw-config", "configMap": {"name": "jiuwenclaw-gateway-config"}},
        {"name": "gw-envfile", "configMap": {"name": "jiuwenclaw-gateway-envfile"}},
        {"name": "data", "persistentVolumeClaim": {"claimName": "jiuwenclaw-pvc"}},
        {"name": "hp-cgroup", "hostPath": {"path": "/sys/fs/cgroup", "type": "Directory"}},
        {"name": "hp-jiuwenbox", "hostPath": {"path": "${CLAW_CODE_PATH}/jiuwenbox/src/jiuwenbox", "type": "Directory"}}
      ]
    }],
    "scopes": [{
      "scope_id": "fallback",
      "index": 100,
      "template_id": "tpl-fallback",
      "routing_rules": ""
    }]
  }
}
EOF

echo ""
echo "=== 3. 等待 agentserver pod 起来(主容器 + jiuwenbox sidecar,2/2 Ready)==="
echo "预期:agent-runtime 收到 config_sync 后,rm_autoscale 下一个 tick(≤1s)"
echo "      会按 min_idle_pods=1 在 ${NAMESPACE} 拉 1 个 agentserver pod(含 jiuwenbox sidecar)。"
echo ""
echo "现在跑:"
echo "  kubectl get pod -n ${NAMESPACE} -w"
echo ""
echo "或直接看 agent-runtime 的拉 pod 日志:"
echo "  kubectl logs -n ${NAMESPACE} ${AGENT_RUNTIME_POD} -f --tail=20 | grep -E 'autoscale|deploy'"
