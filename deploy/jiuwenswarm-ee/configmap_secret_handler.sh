#!/usr/bin/env bash
set -euo >/dev/null 2>&1


render_secret_configmap() {
    local namespace="${DEPLOY_VARS["NAMESPACE"]}"
    local name="${DEPLOY_VARS["SECRET_CM_NAME"]}"
    local file="${CONFIG["SECRET_CM_FILE"]}"

    # 本次部署负责写入的密码键：DB_MODULES 推导 + redis/obs
    local own_keys=()
    local m
    for m in "${DB_MODULES[@]}"; do
        own_keys+=("${m}_DB_PASSWORD")
    done
    own_keys+=("REDIS_PASSWORD" "OBS_SECRET_KEY")

    local key b64

    # 文件不存在（首次部署）→ 完整渲染模板
    # 文件已存在 → yq 只更新自己的密码域，不碰其他键
    if [ ! -f "${file}" ]; then
        for key in "${own_keys[@]}"; do
            [ -z "${DEPLOY_VARS[$key]:-}" ] && continue
            DEPLOY_VARS["${key}_ENCODED"]=$(printf '%s' "${DEPLOY_VARS[$key]}" | base64 -w 0)
        done
        render_config_template "${CONFIG["SECRET_CM_TEMPLATE_FILE"]}" "${file}" "DEPLOY_VARS"
        return
    fi

    for key in "${own_keys[@]}"; do
        [ -n "${DEPLOY_VARS[$key]:-}" ] || continue
        b64=$(printf '%s' "${DEPLOY_VARS[$key]}" | base64 -w 0)
        yq eval ".data[\"${key}\"] = \"${b64}\"" -i "${file}"
    done
    success "Secret configmap is rendered."
}

ensure_secret_configmap() {
    exec_cmd kubectl apply -f ${CONFIG["SECRET_CM_FILE"]}
}

uninstall_secret_configmap() {
    local namespace="${DEPLOY_VARS["NAMESPACE"]}"
    local component_names=(
        "${DEPLOY_VARS["GATEWAY_NAME"]}"
        "${DEPLOY_VARS["MANAGER_SERVER_NAME"]}"
        "${DEPLOY_VARS["WEB_NAME"]}"
        "${DEPLOY_VARS["AGENT_RUNTIME_NAME"]}"
    )

    # Gateway、Web、Manager、AgentRuntime这些组件都依赖于本资源，检查它们是否存在，若存在不能删除本资源
    for cname in "${component_names[@]}"; do
        if check_k8s_resource_exists "deployment" "${cname}" "${namespace}"; then
            warning "Deployment ${namespace}/${cname} exists, skip deleting ${namespace}/${DEPLOY_VARS["SECRET_CM_NAME"]}."
            return
        fi
    done

    delete_k8s_resource_by_file "${CONFIG["SECRET_CM_FILE"]}"
    success "Secret configmap is uninstalled."
}
