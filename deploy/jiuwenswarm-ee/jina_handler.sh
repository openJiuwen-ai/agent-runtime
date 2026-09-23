#!/usr/bin/env bash
set -euo >/dev/null 2>&1

render_jina_files() {
    render_config_template "${CONFIG["JINA_TEMPLATE_FILE"]}" "${CONFIG["JINA_FILE"]}" "DEPLOY_VARS"
    success "Jina module is rendered."
}

deploy_jina() {
    local name="${DEPLOY_VARS["JINA_NAME"]}"

    exec_cmd kubectl apply -f ${CONFIG["JINA_FILE"]}
    wait_k8s_resource_ready "deployment" "${name}-reader"
    wait_k8s_resource_ready "deployment" "${name}-cache-proxy"
    success "Jina module is deployed."
}

uninstall_jina() {
    delete_k8s_resource_by_file "${CONFIG["JINA_FILE"]}"
    success "Jina module is uninstalled."
}
