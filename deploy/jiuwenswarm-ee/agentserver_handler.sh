#!/usr/bin/env bash
set -euo >/dev/null 2>&1

# ==== AgentServer env ConfigMap（动态/静态两种形态共用；runtime_handler 亦调用）====
render_agentserver_env_configmap() {
    local env_file="${CONFIG["AS_ENV_FILE"]}"
    local yaml_file="${CONFIG["AS_ENV_YAML_FILE"]}"

    render_config_template "${CONFIG["AS_ENV_TEMPLATE_FILE"]}" "${env_file}" "DEPLOY_VARS"

    # 移除所有注释行、过滤空值行 KEY=、按变量名排序
    # 注意：不能 sort > 同一个文件，shell 会在管道启动前就截断输出文件，
    # 导致左侧 grep 读到空。先写临时文件再 mv 覆盖。
    grep -v '^[[:space:]]*#' "${env_file}" \
        | grep '=' \
        | awk -F'=' '$2 != ""' \
        | sort > "${env_file}.tmp" && mv -f "${env_file}.tmp" "${env_file}"

    kubectl create configmap -n "${DEPLOY_VARS["NAMESPACE"]}" "${DEPLOY_VARS["AGENT_SERVER_ENV_CM_NAME"]}" \
        --from-env-file="${env_file}" \
        --dry-run=client -o yaml \
        | yq eval 'del(.metadata.creationTimestamp)' > "${yaml_file}"
    success "AgentServer env ConfigMap rendered: ${yaml_file}"
}

create_agentserver_env_configmap() {
    exec_cmd kubectl apply -f "${CONFIG["AS_ENV_YAML_FILE"]}"
}

delete_agentserver_env_configmap() {
    delete_k8s_resource_by_file "${CONFIG["AS_ENV_YAML_FILE"]}"
}

# ==== AgentServer 静态模块（AGENT_RUNTIME_ENABLED=false 时经此部署）====
# 依赖检查 check_agentserver_up_dependency 在 check_handler.sh（与其他模块同址）

gen_agentserver_file() {
    local file="${CONFIG["AS_FILE"]}"

    render_config_template "${CONFIG["AS_TEMPLATE_FILE"]}" "${file}" "DEPLOY_VARS"
    enable_dev_mode_if_needed "${file}" agentserver

    # data 卷按存储模式二选一（与 patch_handler.inject_data_volume 语义对齐）：
    #   pvc: 模板默认（claimName=CLAW_PVC）
    #   nfs: 直连 NFS 共享目录（server=NFS_SERVER_ADDR, path=<share>/jiuwenclaw/<NAMESPACE>）
    if [ "${DEPLOY_VARS["CLAW_MOUNT_TYPE"]}" == "nfs" ]; then
        if [ -z "${DEPLOY_VARS["NFS_SERVER_ADDR"]:-}" ]; then
            error "CLAW_MOUNT_TYPE=nfs requires NFS_SERVER_ADDR (external) or a deployed built-in NFS"
        fi
        local nfs_share="${DEPLOY_VARS["NFS_SHARE_PATH"]%/}"
        local nfs_path="${nfs_share}/jiuwenclaw/${DEPLOY_VARS["NAMESPACE"]}"
        yq eval 'select(.kind == "Deployment").spec.template.spec.volumes |= map(select(.name == "data") |= {"name": "data", "nfs": {"server": "'"${DEPLOY_VARS["NFS_SERVER_ADDR"]}"'", "path": "'"${nfs_path}"'"}} // .)' -i "${file}"
    fi

    # 沙箱可插拔：JIUWENBOX_ENABLED=false 时移除 jiuwenbox sidecar 及其 hp-cgroup 卷
    if [ "${DEPLOY_VARS["JIUWENBOX_ENABLED"]}" != "true" ]; then
        yq eval 'select(.kind == "Deployment").spec.template.spec.containers
            |= map(select(.name != "'"${DEPLOY_VARS["JIUWENBOX_NAME"]}"'"))' -i "${file}"
        yq eval 'select(.kind == "Deployment").spec.template.spec.volumes
            |= map(select(.name != "hp-cgroup"))' -i "${file}"
    fi

    add_resource_if_set "AGENT_SERVER" "${file}"
    success "AgentServer file generation completed: ${file}"
}

render_agentserver_files() {
    render_secret_configmap
    render_agentserver_env_configmap
    gen_agentserver_file

    # 内置 PVC 渲染归属 agentserver 模块（静态 pod 挂数据卷）
    if [[ "${DEPLOY_VARS["CLAW_MOUNT_TYPE"]}" == "pvc" && "${DEPLOY_VARS["ENABLE_EXTERNAL_PVC"]}" == "false" ]]; then
        render_config_template "${CONFIG["CLAW_PVC_TEMPLATE_FILE"]}" "${CONFIG["CLAW_PVC_FILE"]}" "DEPLOY_VARS"
    fi
    success "AgentServer module is rendered."
}

deploy_agentserver() {
    local pvc_file="${CONFIG["CLAW_PVC_FILE"]}"

    ensure_secret_configmap
    # PVC 先于 agentserver 部署：数据卷挂载依赖它
    if [[ "${DEPLOY_VARS["CLAW_MOUNT_TYPE"]}" == "pvc" && "${DEPLOY_VARS["ENABLE_EXTERNAL_PVC"]}" == "false" && -f "${pvc_file}" ]]; then
        exec_cmd kubectl apply -f "${pvc_file}"
    fi
    create_agentserver_env_configmap
    exec_cmd kubectl apply -f "${CONFIG["AS_FILE"]}"
    wait_k8s_resource_ready "deployment" "${DEPLOY_VARS["AGENT_SERVER_NAME"]}" "${DEPLOY_VARS["NAMESPACE"]}"
    success "AgentServer module is deployed."
}

uninstall_agentserver() {
    delete_k8s_resource_by_file "${CONFIG["AS_FILE"]}"
    delete_agentserver_env_configmap
    uninstall_secret_configmap

    # 仅在内置 PVC 时删（外部 PVC 不动）。必须最后执行：
    # PVC 带 pvc-protection finalizer，agentserver pod 释放挂载前删除会一直阻塞
    if [[ "${DEPLOY_VARS["CLAW_MOUNT_TYPE"]}" == "pvc" && -z "${DEPLOY_VARS["CLAW_PVC"]:-}" && -f "${CONFIG["CLAW_PVC_FILE"]}" ]]; then
        delete_k8s_resource_by_file "${CONFIG["CLAW_PVC_FILE"]}"
    fi
    success "AgentServer module is uninstalled."
}
