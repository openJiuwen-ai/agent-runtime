#!/usr/bin/env bash
set -euo >/dev/null 2>&1

render_mariadb_files() {
    local file="${CONFIG["MARIADB_FILE"]}"

    ensure_available_port "MARIADB_NODE_PORT"
    render_config_template "${CONFIG["MARIADB_TEMPLATE_FILE"]}" "${file}" "DEPLOY_VARS"
    add_resource_if_set "MARIADB" "${file}"
    success "MariaDB module is rendered."
}

deploy_mariadb() {
    exec_cmd kubectl apply -f ${CONFIG["MARIADB_FILE"]}
    wait_k8s_resource_ready "statefulset" "${DEPLOY_VARS["MARIADB_NAME"]}"
    success "MARIADB_NODE_PORT: ${DEPLOY_VARS["MARIADB_NODE_PORT"]}"
    success "MariaDB module is deployed."
}

uninstall_mariadb() {
    delete_k8s_resource_by_file "${CONFIG["MARIADB_FILE"]}"
    success "MariaDB module is uninstalled."
}
