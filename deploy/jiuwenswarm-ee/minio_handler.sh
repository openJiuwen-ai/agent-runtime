#!/usr/bin/env bash
set -euo >/dev/null 2>&1

render_minio_files() {

    ensure_available_port "MINIO_API_NODE_PORT" "MINIO_CONSOLE_NODE_PORT"
    render_config_template "${CONFIG["MINIO_TEMPLATE_FILE"]}" "${CONFIG["MINIO_FILE"]}" "DEPLOY_VARS"
    success "MinIO module is rendered."
}

deploy_minio() {

    exec_cmd kubectl apply -f ${CONFIG["MINIO_FILE"]}
    wait_k8s_resource_ready "statefulset" "${DEPLOY_VARS["MINIO_NAME"]}"
    success "MINIO_API_NODE_PORT: ${DEPLOY_VARS["MINIO_API_NODE_PORT"]}"
    success "MINIO_CONSOLE_NODE_PORT: ${DEPLOY_VARS["MINIO_CONSOLE_NODE_PORT"]}"
    success "MinIO module is deployed."
}

uninstall_minio() {

    delete_k8s_resource_by_file "${CONFIG["MINIO_FILE"]}"
    success "MinIO module is uninstalled."
}
