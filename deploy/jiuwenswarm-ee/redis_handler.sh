#!/usr/bin/env bash
set -euo >/dev/null 2>&1

render_redis_files() {
    ensure_available_port "REDIS_NODE_PORT"
    render_config_template "${CONFIG["REDIS_TEMPLATE_FILE"]}" "${CONFIG["REDIS_FILE"]}" "DEPLOY_VARS"
    success "Redis module is rendered."
}

deploy_redis() {
    exec_cmd kubectl apply -f "${CONFIG["REDIS_FILE"]}"
    wait_k8s_resource_ready "deployment" "${DEPLOY_VARS["REDIS_NAME"]}" "${DEPLOY_VARS["NAMESPACE"]}"
    success "Redis module is deployed."
}

uninstall_redis() {
    delete_k8s_resource_by_file "${CONFIG["REDIS_FILE"]}"
    success "Redis module is uninstalled."
}


# gateway / runtime 两个模块共用同一份内置 Redis，不能贸然关停。
# 只有两个模块 都关停之后，才能关停
ensure_redis_down() {
    local namespace="${DEPLOY_VARS["NAMESPACE"]}"
    local name="${DEPLOY_VARS["REDIS_NAME"]}"

    # 外挂 Redis 由用户自行管理，本工具不负责卸载
    if [ "${DEPLOY_VARS["ENABLE_EXTERNAL_REDIS"]}" == "true" ]; then
        info "External Redis in use, skip shutting down built-in Redis."
        return
    fi

    # 当前命名空间内已无内置 Redis Deployment，说明已卸载或从未部署
    if ! check_k8s_resource_exists "deployment" "${name}" "${namespace}"; then
        info "Built-in Redis '${name}' not found in namespace '${namespace}', nothing to do."
        return
    fi

    for dname in ${DEPLOY_VARS["GATEWAY_NAME"]} ${DEPLOY_VARS["AGENT_RUNTIME_NAME"]}
    do
        if check_k8s_resource_exists "deployment" "${dname}" "${namespace}"; then
            info " ${dname} still running in namespace '${namespace}', keep Redis alive."
            return
        fi
    done
    uninstall_redis
    success "Built-in Redis '${name}' has been shut down."
}
