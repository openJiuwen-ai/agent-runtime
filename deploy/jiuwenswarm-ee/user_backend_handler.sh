#!/usr/bin/env bash

check_user_backend_up_dependency() {
    check_if_db_up
    local db_type="${DEPLOY_VARS[DB_TYPE]}"
    set_db_var MANAGER "$db_type" DB_NAME
    set_db_var MANAGER "$db_type" PG_SCHEMA
}

render_user_backend_files() {
    DEPLOY_VARS[USER_BACKEND_DB_PASSWORD_ENCODED]=$(printf '%s' "${DEPLOY_VARS[USER_BACKEND_DB_PASSWORD]}" | base64 -w 0)
    local file="${CONFIG[USER_BACKEND_FILE]}"
    render_config_template "${CONFIG[USER_BACKEND_TEMPLATE_FILE]}" "$file" DEPLOY_VARS
    enable_dev_mode_if_needed "$file" user-backend
}

deploy_user_backend() {
    exec_cmd kubectl apply -f "${CONFIG[USER_BACKEND_FILE]}"
    info "User backend deployed. Kubernetes will mark it ready once Identity and the database views are available."
}

uninstall_user_backend() {
    delete_k8s_resource_by_file "${CONFIG[USER_BACKEND_FILE]}"
}
