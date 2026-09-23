#!/usr/bin/env bash
set -euo >/dev/null 2>&1

gen_gateway_env_file() {
    local env_file="${CONFIG["GATEWAY_ENV_FILE"]}"

    render_config_template "${CONFIG["GATEWAY_ENV_TEMPLATE_FILE"]}" "${env_file}" "DEPLOY_VARS"

    # 移除所有注释行、过滤空值行 KEY=、按变量名排序
    # 注意：不能 sort > 同一个文件，shell 会在管道启动前就截断输出文件，
    # 导致左侧 grep 读到空。先写临时文件再 mv 覆盖。
    grep -v '^[[:space:]]*#' "${env_file}" \
        | grep '=' \
        | awk -F'=' '$2 != ""' \
        | sort > "${env_file}.tmp" && mv -f "${env_file}.tmp" "${env_file}"

    kubectl create configmap -n "${DEPLOY_VARS["NAMESPACE"]}" "${DEPLOY_VARS["GATEWAY_ENV_FILE_CM_NAME"]}" \
        --from-env-file="${env_file}" \
        --dry-run=client -o yaml \
        | yq eval 'del(.metadata.creationTimestamp)' > "${CONFIG["GATEWAY_ENV_YAML_FILE"]}"
}

gen_gateway_file() {
    local mode="${DEPLOY_VARS["MODE"]}"
    local file="${CONFIG["GATEWAY_FILE"]}"

    render_config_template "${CONFIG["GATEWAY_TEMPLATE_FILE"]}" "${file}" "DEPLOY_VARS"
    enable_dev_mode_if_needed "${file}" gateway

    # No need to install packages
    if [[ "${mode}" == "dev" && -n "${DEPLOY_VARS["CLAW_CODE_PATH"]:-}" ]]; then
        local claw_code="${DEPLOY_VARS["CLAW_CODE_PATH"]}"
        yq eval '.dependencies = {}' -i "${claw_code}/packages/jiuwenclaw-ee/gateway/extensions/runtime_management_extension/extension.yaml"
        yq eval '.dependencies = {}' -i "${claw_code}/packages/jiuwenclaw-ee/gateway/extensions/manager_config_receiver/extension.yaml"
    fi

    add_resource_if_set "GATEWAY" "${file}"

    if [[ "${mode}" != "dev" && "${DEPLOY_VARS["GATEWAY_SCHED_LABEL_ENABLED"]}" == "true" ]]; then
        # Automatically create nodeSelector and set gateway=enable
        yq eval 'select(.kind == "Deployment").spec.template.spec.nodeSelector |= {"gateway": "enable"}' -i "${file}"
    fi

    if [[ "${DEPLOY_VARS["APPLY_PATCH"]}" != "true" ]]; then
        yq eval-all -i 'select(.kind != "Service" or .spec.type != "NodePort")' "${file}"
    fi

    success "Gateway file generation completed: ${file}"
}

render_gateway_files() {

    render_secret_configmap
    gen_gateway_env_file

    ensure_available_port "GATEWAY_CONFIG_HTTP_NODE_PORT"
    gen_gateway_file
    if [[ "${DEPLOY_VARS[JIUWENSWARM_LINK_MTLS_MODE]}" != off ]]; then
        link_mtls_render gateway "${CONFIG[GATEWAY_FILE]}"
    fi
    success "Gateway module is rendered."
}

deploy_gateway() {

    ensure_secret_configmap
    # 使用 apply 保证重复部署幂等：ConfigMap 已存在时更新内容，不因 create 冲突失败。
    exec_cmd kubectl apply -f "${CONFIG["GATEWAY_ENV_YAML_FILE"]}"

    exec_cmd kubectl apply -f "${CONFIG["GATEWAY_FILE"]}"
    wait_k8s_resource_ready "deployment" "${DEPLOY_VARS["GATEWAY_NAME"]}" "${DEPLOY_VARS["NAMESPACE"]}"
    success "Gateway module is deployed."
}

uninstall_gateway() {

    delete_k8s_resource_by_file "${CONFIG["GATEWAY_FILE"]}"
    delete_k8s_resource_by_file "${CONFIG["GATEWAY_ENV_YAML_FILE"]}"
    uninstall_secret_configmap
    ensure_redis_down
    success "Gateway module is uninstalled."
}
