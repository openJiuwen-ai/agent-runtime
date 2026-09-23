#!/usr/bin/env bash
set -euo >/dev/null 2>&1

render_postgresql_files() {
    local file="${CONFIG["POSTGRESQL_FILE"]}"

    ensure_available_port "POSTGRESQL_NODE_PORT"
    render_config_template "${CONFIG["POSTGRESQL_TEMPLATE_FILE"]}" "${file}" "DEPLOY_VARS"
    add_resource_if_set "POSTGRESQL" "${file}"
    success "PostgreSQL module is rendered."
}

deploy_postgresql() {
    
    exec_cmd kubectl apply -f ${CONFIG["POSTGRESQL_FILE"]}
    wait_k8s_resource_ready "statefulset" "${DEPLOY_VARS["POSTGRESQL_NAME"]}"
    success "POSTGRESQL_NODE_PORT: ${DEPLOY_VARS["POSTGRESQL_NODE_PORT"]}"
    success "PostgreSQL module is deployed."
}

uninstall_postgresql() {

    delete_k8s_resource_by_file "${CONFIG["POSTGRESQL_FILE"]}"
    success "PostgreSQL module is uninstalled."
}
