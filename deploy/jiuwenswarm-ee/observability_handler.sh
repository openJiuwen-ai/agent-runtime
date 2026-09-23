#!/usr/bin/env bash
set -euo >/dev/null 2>&1

# 监控栈语义（loki 是 otel collector 的日志后端，二者必须成对出现）：
#   OTEL_ENABLED=false              → 整个监控栈（otel/loki/UI）不渲染不部署
#   ENABLE_EXTERNAL_OTEL=true       → collector/loki 均由外部承担，内置栈全部跳过
#   ENABLE_EXTERNAL_LOKI=true       → 内置 otel 保留，日志落外部 Loki，UI 指向 <<LOKI_URL>>
#   全内置                          → otel + loki + UI

render_otel_files() {
    if [ "${DEPLOY_VARS["OTEL_ENABLED"]}" == "false" ]; then
        return
    fi
    if [ "${DEPLOY_VARS["ENABLE_EXTERNAL_OTEL"]}" == "true" ]; then
        return
    fi
    render_config_template "${CONFIG["OTEL_TEMPLATE_FILE"]}" "${CONFIG["OTEL_FILE"]}" "DEPLOY_VARS"


    if [ "${DEPLOY_VARS["ENABLE_EXTERNAL_LOKI"]}" == "false" ]; then
        render_config_template "${CONFIG["LOKI_TEMPLATE_FILE"]}" "${CONFIG["LOKI_FILE"]}" "DEPLOY_VARS"
    fi
    success "Otel module is rendered."
}

render_monitor_files() {
    ensure_available_port "OBSERVABILITY_NODE_PORT"
    render_otel_files
    render_config_template "${CONFIG["OBSERVABILITY_TEMPLATE_FILE"]}" "${CONFIG["OBSERVABILITY_FILE"]}" "DEPLOY_VARS"
    enable_dev_mode_if_needed "${CONFIG["OBSERVABILITY_FILE"]}" observability
    success "Monitor module is rendered."
}

deploy_otel() {
    local namespace="${DEPLOY_VARS["NAMESPACE"]}"
    if [ "${DEPLOY_VARS["OTEL_ENABLED"]}" == "false" ]; then
        return
    fi
    if [ "${DEPLOY_VARS["ENABLE_EXTERNAL_OTEL"]}" == "true" ]; then
        return
    fi
    exec_cmd kubectl apply -f ${CONFIG["OTEL_FILE"]}
    wait_k8s_resource_ready "daemonset" "${DEPLOY_VARS["OTEL_NAME"]}" "${namespace}"


    if [ "${DEPLOY_VARS["ENABLE_EXTERNAL_LOKI"]}" == "false" ]; then
        exec_cmd kubectl apply -f ${CONFIG["LOKI_FILE"]}
        wait_k8s_resource_ready "statefulset" "${DEPLOY_VARS["LOKI_NAME"]}" "${namespace}"
    fi
    success "Otel module is deployed."
}

deploy_monitor() {
    deploy_otel
    exec_cmd kubectl apply -f ${CONFIG["OBSERVABILITY_FILE"]}
    wait_k8s_resource_ready "deployment" "${DEPLOY_VARS["OBSERVABILITY_NAME"]}" "${DEPLOY_VARS["NAMESPACE"]}"
    success "OBSERVABILITY_NODE_PORT: ${DEPLOY_VARS["OBSERVABILITY_NODE_PORT"]}"
}

uninstall_otel() {
    if [ "${DEPLOY_VARS["OTEL_ENABLED"]}" == "false" ]; then
        return
    fi
    if [ "${DEPLOY_VARS["ENABLE_EXTERNAL_OTEL"]}" == "true" ]; then
        return
    fi
    delete_k8s_resource_by_file "${CONFIG["OTEL_FILE"]}"

    # loki 带 NFS PV，由 delete_k8s_resource_by_file 按
    # 工作负载→Pod 退出→PVC→PV→剩余资源 顺序卸载
    if [ "${DEPLOY_VARS["ENABLE_EXTERNAL_LOKI"]}" == "false" ]; then
        delete_k8s_resource_by_file "${CONFIG["LOKI_FILE"]}"
    fi
    success "Otel module is uninstalled."
}

uninstall_monitor() {
    uninstall_otel
    delete_k8s_resource_by_file "${CONFIG["OBSERVABILITY_FILE"]}"
    success "Monitor module is uninstalled."
}
