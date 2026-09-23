#!/usr/bin/env bash
set -euo >/dev/null 2>&1

render_mysql_files() {
    local file="${CONFIG["MYSQL_FILE"]}"

    ensure_available_port "MYSQL_NODE_PORT"
    render_config_template "${CONFIG["MYSQL_TEMPLATE_FILE"]}" "${file}" "DEPLOY_VARS"
    add_resource_if_set "MYSQL" "${file}"
    success "MySQL module is rendered."
}

deploy_mysql() {

    exec_cmd kubectl apply -f ${CONFIG["MYSQL_FILE"]}
    wait_k8s_resource_ready "statefulset" "${DEPLOY_VARS["MYSQL_NAME"]}"
    success "MYSQL_NODE_PORT: ${DEPLOY_VARS["MYSQL_NODE_PORT"]}"
    success "MySQL module is deployed."
}

uninstall_mysql() {

    delete_k8s_resource_by_file "${CONFIG["MYSQL_FILE"]}"
    success "MySQL module is uninstalled."
}


