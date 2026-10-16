#!/usr/bin/env bash
set -euo >/dev/null 2>&1

# MariaDB sql_mode 注入：仅当 MARIADB_SQL_MODE 有值时追加 --sql-mode 参数（对齐特定客户环境用）
#   默认空 → 保持模板原样（MariaDB 内置默认 sql_mode）
set_mariadb_sql_mode_if_needed() {
    local file="${CONFIG["MARIADB_FILE"]}"

    if [ -z "${DEPLOY_VARS["MARIADB_SQL_MODE"]:-}" ]; then
        return
    fi

    yq eval 'select(.kind == "StatefulSet").spec.template.spec.containers[0].args |= (. + ["--sql-mode='"${DEPLOY_VARS["MARIADB_SQL_MODE"]}"'"])' -i "${file}"
    yq eval 'select(.kind == "StatefulSet").spec.template.spec.containers[0].args[] style = "double"' -i "${file}"
}

render_mariadb_files() {
    local file="${CONFIG["MARIADB_FILE"]}"

    ensure_available_port "MARIADB_NODE_PORT"
    render_config_template "${CONFIG["MARIADB_TEMPLATE_FILE"]}" "${file}" "DEPLOY_VARS"
    set_mariadb_sql_mode_if_needed
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
