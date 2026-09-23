#!/usr/bin/env bash
set -euo >/dev/null 2>&1

render_log_files() {
    if [ -z "${DEPLOY_VARS["CLAW_LOG_DIR"]:-}" ]; then
        DEPLOY_VARS["CLAW_LOG_DIR"]="${HOME}/claw_logs"
    fi

    # 根据脱敏开关决定 file_out sink 的输入来源：
    #   COLLECT_LOG_MASK_ENABLED=true  → mask_sensitive（先脱敏再落盘）
    #   COLLECT_LOG_MASK_ENABLED=false → filter_and_parse（跳过脱敏，直接落盘）
    if [ "${DEPLOY_VARS["COLLECT_LOG_MASK_ENABLED"]}" == "false" ]; then
        DEPLOY_VARS["LOG_SINK_INPUT"]="filter_and_parse"
    else
        DEPLOY_VARS["LOG_SINK_INPUT"]="mask_sensitive"
    fi

    rt=$(kubectl get node "${DEPLOY_VARS["CURRENT_NODE_NAME"]}" -o jsonpath='{.status.nodeInfo.containerRuntimeVersion}')
    case "${rt}" in
        containerd://*) DEPLOY_VARS["VAR_LIB_DOCKER_PATH"]="/var/lib/containerd" ;;
        docker://*)     DEPLOY_VARS["VAR_LIB_DOCKER_PATH"]="/var/lib/docker/containers" ;;
    esac

    render_config_template "${CONFIG["LOG_TEMPLATE_FILE"]}" "${CONFIG["LOG_FILE"]}" "DEPLOY_VARS"
    success "Log module is rendered."
}

deploy_log() {
    exec_cmd kubectl apply -f ${CONFIG["LOG_FILE"]}
    wait_k8s_resource_ready "deployment" "${DEPLOY_VARS["VECTOR_NAME"]}"
    wait_k8s_resource_ready "daemonset" "${DEPLOY_VARS["FLUENT_BIT_NAME"]}"
    success "Log module is deployed."
}

uninstall_log() {
    delete_k8s_resource_by_file "${CONFIG["LOG_FILE"]}"
    success "Log module is uninstalled."
}
