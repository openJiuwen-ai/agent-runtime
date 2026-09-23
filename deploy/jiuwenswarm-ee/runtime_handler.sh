#!/usr/bin/env bash
set -euo >/dev/null 2>&1

render_agentserver_env_configmap() {
    local env_file="${CONFIG["AS_ENV_FILE"]}"
    local yaml_file="${CONFIG["AS_ENV_YAML_FILE"]}"

    render_config_template "${CONFIG["AS_ENV_TEMPLATE_FILE"]}" "${env_file}" "DEPLOY_VARS"

    # 移除所有注释行、过滤空值行 KEY=、按变量名排序
    # 注意：不能 sort > 同一个文件，shell 会在管道启动前就截断输出文件，
    # 导致左侧 grep 读到空。先写临时文件再 mv 覆盖。
    grep -v '^[[:space:]]*#' "${env_file}" \
        | grep '=' \
        | awk -F'=' '$2 != ""' \
        | sort > "${env_file}.tmp" && mv -f "${env_file}.tmp" "${env_file}"

    kubectl create configmap -n "${DEPLOY_VARS["NAMESPACE"]}" "${DEPLOY_VARS["AGENT_SERVER_ENV_CM_NAME"]}" \
        --from-env-file="${env_file}" \
        --dry-run=client -o yaml \
        | yq eval 'del(.metadata.creationTimestamp)' > "${yaml_file}"
    success "AgentServer env ConfigMap rendered: ${yaml_file}"
}

create_agentserver_env_configmap() {
    exec_cmd kubectl apply -f "${CONFIG["AS_ENV_YAML_FILE"]}"
}

delete_agentserver_env_configmap() {
    delete_k8s_resource_by_file "${CONFIG["AS_ENV_YAML_FILE"]}"
}

gen_runtime_file() {
    local file="${CONFIG["RUNTIME_FILE"]}"

    local redis_host="${DEPLOY_VARS["REDIS_HOST"]}"
    local redis_port="${DEPLOY_VARS["REDIS_PORT"]}"

    if [[ "${DEPLOY_VARS["REDIS_MODE"]}" == "cluster" ]]; then
        DEPLOY_VARS["OPENJIUWEN_SERVICE_REDIS_URL"]="redis+cluster://${redis_host}:${redis_port}"
    else
        DEPLOY_VARS["OPENJIUWEN_SERVICE_REDIS_URL"]="redis://${redis_host}:${redis_port}/${DEPLOY_VARS["AGENT_RUNTIME_REDIS_DB"]}"
    fi

    render_config_template "${CONFIG["RUNTIME_TEMPLATE_FILE"]}" "${file}" "DEPLOY_VARS"
    enable_dev_mode_if_needed ${file} runtime
    if [ "${DEPLOY_VARS["DB_TYPE"]}" == "postgresql" ]; then
        yq eval '
        select(.kind == "Deployment").spec.template.spec.containers[0].env += [
            {
                "name": "OPENJIUWEN_SERVICE_PG_SCHEMA",
                "value": "'"${DEPLOY_VARS["RUNTIME_PG_SCHEMA"]}"'"
            }
        ]' -i "${file}"
    fi

    add_resource_if_set "AGENT_RUNTIME" "${file}"
    if [[ "${DEPLOY_VARS["APPLY_PATCH"]}" != "true" ]]; then
        yq eval-all -i 'select(.kind != "Service" or .spec.type != "NodePort")' "${file}"
    fi
}

render_runtime_files() {

    render_secret_configmap
    ensure_available_port "AGENT_RUNTIME_NODE_PORT"
    gen_runtime_file
    render_patch_file
    render_agentserver_env_configmap
    if [[ "${DEPLOY_VARS[JIUWENSWARM_LINK_MTLS_MODE]}" != off ]]; then
        link_mtls_render runtime "${CONFIG[RUNTIME_FILE]}"
        link_mtls_render agentserver "${CONFIG[AS_JSON_FILE]}"
    fi

    # agentserver 由 runtime 动态创建并挂载内置 PVC，PVC 渲染归属 runtime
    if [[ "${DEPLOY_VARS["CLAW_MOUNT_TYPE"]}" == "pvc" && "${DEPLOY_VARS["ENABLE_EXTERNAL_PVC"]}" == "false" ]]; then
        render_config_template "${CONFIG["CLAW_PVC_TEMPLATE_FILE"]}" "${CONFIG["CLAW_PVC_FILE"]}" "DEPLOY_VARS"
    fi
    success "Runtime module is rendered."
}

deploy_runtime() {
    local pvc_file="${CONFIG["CLAW_PVC_FILE"]}"

    ensure_secret_configmap
    # PVC 先于 runtime 部署：后续 config_sync 创建的 agentserver pod 会挂载它
    if [[ "${DEPLOY_VARS["CLAW_MOUNT_TYPE"]}" == "pvc" && "${DEPLOY_VARS["ENABLE_EXTERNAL_PVC"]}" == "false" && -f "${CONFIG["CLAW_PVC_FILE"]}" ]]; then
        exec_cmd kubectl apply -f "${CONFIG["CLAW_PVC_FILE"]}"
    fi
    exec_cmd kubectl apply -f ${CONFIG["RUNTIME_FILE"]}
    wait_k8s_resource_ready "deployment" "${DEPLOY_VARS["AGENT_RUNTIME_NAME"]}" "${DEPLOY_VARS["NAMESPACE"]}"
    create_agentserver_env_configmap
    install_patch
    success "Runtime module is deployed."
}

uninstall_runtime() {
    local namespace="${DEPLOY_VARS["NAMESPACE"]}"
    local name="${DEPLOY_VARS["AGENT_RUNTIME_NAME"]}"
    local pvc_file="${CONFIG["CLAW_PVC_FILE"]}"

    delete_k8s_resource_by_file "${CONFIG["RUNTIME_FILE"]}"

    # 兜底清理 runtime 动态创建的 agentserver pod（按 label）
    local orphan_pods
    orphan_pods=$(kubectl get pods -n "${namespace}" -l jiuwenclaw-component=agentserver -o name 2>/dev/null || true)
    if [ -n "${orphan_pods}" ]; then
        info "Cleaning up orphan agentserver pods created by runtime (label: jiuwenclaw-component=agentserver)"
        exec_cmd kubectl delete pod -n "${namespace}" -l jiuwenclaw-component=agentserver --ignore-not-found=true
    else
        info "No orphan agentserver pods to clean up."
    fi

    uninstall_secret_configmap
    ensure_redis_down
    delete_agentserver_env_configmap

    # 仅在内置 PVC 时删（外部 PVC 不动）。必须在孤儿 agentserver pod 清理之后执行：
    # PVC 带 pvc-protection finalizer，agentserver pod 释放挂载前删除此 PVC 
    # 会一直阻塞
    if [[ "${DEPLOY_VARS["CLAW_MOUNT_TYPE"]}" == "pvc" && -z "${DEPLOY_VARS["CLAW_PVC"]:-}" && -f "${pvc_file}" ]]; then
        delete_k8s_resource_by_file "${pvc_file}"
    fi
    success "Runtime module is uninstalled."
}
