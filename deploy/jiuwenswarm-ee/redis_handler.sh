#!/usr/bin/env bash
set -euo >/dev/null 2>&1

# 内置 Redis 认证参数注入：与客户端（gateway/runtime）共用 REDIS_PASSWORD / REDIS_USER。
#   REDIS_USER + REDIS_PASSWORD 均配置 → --requirepass + ACL 用户（on >密码 ~* &* +@all）
#   仅配 REDIS_USER 缺 REDIS_PASSWORD  → 渲染期报错（ACL 用户必须带密码）
#   未配置 REDIS_USER                  → 维持无认证裸跑（此时客户端也不要配密码，否则 AUTH 会失败）
# 注意：
#   1. yq 必须用 select 限定 Deployment（裸路径会给 Service 也造出 spec.template）
#   2. 密码经 shell 插值进 yq 表达式，含双引号/反斜杠的密码不支持
#   3. yq 按 YAML 1.2 写 "on" 这类字符串不会加引号，k8s 按 1.1 解析会变成 bool
#      （args 要求 string）→ 末尾统一给 args 元素强制双引号样式
set_redis_acl_if_needed() {
    local file="${CONFIG["REDIS_FILE"]}"

    # 未配置用户名 → 保持模板原样，走 default 用户（与历史行为一致）
    if [ -z "${DEPLOY_VARS["REDIS_USER"]:-}" ]; then
        return
    fi

    if [ -z "${DEPLOY_VARS["REDIS_PASSWORD"]:-}" ]; then
        error "REDIS_USER requires REDIS_PASSWORD to be set as well (an ACL user must have a password)"
    fi

    yq eval 'select(.kind == "Deployment").spec.template.spec.containers[0].args |= (. + ["--requirepass", "'"${DEPLOY_VARS["REDIS_PASSWORD"]}"'"])' -i "${file}"
    yq eval 'select(.kind == "Deployment").spec.template.spec.containers[0].args |= (. + ["--user", "'"${DEPLOY_VARS["REDIS_USER"]}"'", "on", ">'"${DEPLOY_VARS["REDIS_PASSWORD"]}"'", "~*", "&*", "+@all"])' -i "${file}"
    yq eval 'select(.kind == "Deployment").spec.template.spec.containers[0].args[] style = "double"' -i "${file}"
}

render_redis_files() {
    ensure_available_port "REDIS_NODE_PORT"
    render_config_template "${CONFIG["REDIS_TEMPLATE_FILE"]}" "${CONFIG["REDIS_FILE"]}" "DEPLOY_VARS"
    set_redis_acl_if_needed
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
