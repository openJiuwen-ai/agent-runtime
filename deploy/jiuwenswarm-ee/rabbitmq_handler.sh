#!/usr/bin/env bash
set -euo >/dev/null 2>&1

render_rabbitmq_files() {

    DEPLOY_VARS["RABBITMQ_USER"]=$(echo -n "${DEPLOY_VARS["RABBITMQ_USER"]}" | base64)
    DEPLOY_VARS["RABBITMQ_PASSWORD"]=$(echo -n "${DEPLOY_VARS["RABBITMQ_PASSWORD"]}" | base64)
    ensure_available_port "RABBITMQ_AMQ_NODE_PORT" "RABBITMQ_MGR_NODE_PORT"
    render_config_template "${CONFIG["RABBITMQ_TEMPLATE_FILE"]}" "${CONFIG["RABBITMQ_FILE"]}" "DEPLOY_VARS"
    success "RabbitMQ module is rendered."
}

deploy_rabbitmq() {
    
    exec_cmd kubectl apply -f ${CONFIG["RABBITMQ_FILE"]}
    wait_k8s_resource_ready "statefulset" "${DEPLOY_VARS["RABBITMQ_NAME"]}"
    success "RABBITMQ_AMQ_NODE_PORT: ${DEPLOY_VARS["RABBITMQ_AMQ_NODE_PORT"]}"
    success "RABBITMQ_MGR_NODE_PORT: ${DEPLOY_VARS["RABBITMQ_MGR_NODE_PORT"]}"
    success "RabbitMQ module is deployed."
}

uninstall_rabbitmq() {

    delete_k8s_resource_by_file "${CONFIG["RABBITMQ_FILE"]}"
    success "RabbitMQ module is uninstalled."
}


