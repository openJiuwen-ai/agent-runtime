#!/usr/bin/env bash
set -euo >/dev/null 2>&1

# =====================================================================
# HPA 模块 = metrics-server 集群级基础服务（全集群共享、只需启动一次，
# 部署在 kube-system、忽略 -n 参数）。
# 指标就绪后，gateway / web / agent-runtime /manager-web 的自动扩缩容
# 由各组件 *_HPA_ENABLED 开关控制
# =====================================================================

gen_hpa_file() {
    local file="${CONFIG["HPA_FILE"]}"

    render_config_template "${CONFIG["HPA_TEMPLATE_FILE"]}" "${file}" "DEPLOY_VARS"
    add_resource_if_set "HPA" "${file}"
    success "HPA (metrics-server) file generation completed: ${file}"
}

render_hpa_files() {
    gen_hpa_file
    success "HPA module is rendered."
}

deploy_hpa() {
    exec_cmd kubectl apply -f "${CONFIG["HPA_FILE"]}"
    wait_k8s_resource_ready "deployment" "${DEPLOY_VARS["HPA_NAME"]}" "${DEPLOY_VARS["HPA_NAMESPACE"]}"
    success "HPA module is deployed."
}

uninstall_hpa() {
    delete_k8s_resource_by_file "${CONFIG["HPA_FILE"]}"
    success "HPA module is uninstalled."
}
