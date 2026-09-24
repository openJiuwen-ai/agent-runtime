#!/usr/bin/env bash
set -euo >/dev/null 2>&1

render_nfs_files() {

    render_config_template ${CONFIG["NFS_TEMPLATE_FILE"]} ${CONFIG["NFS_FILE"]} "DEPLOY_VARS"
    success "NFS module is rendered."
}

# NFS is on default namespace
deploy_nfs() {
    local nfs_path=${DEPLOY_VARS["NFS_HOST_PATH"]}

    exec_cmd mkdir -p ${nfs_path}
    exec_cmd chmod -R 777 ${nfs_path}
    exec_cmd kubectl apply -f ${CONFIG["NFS_FILE"]}
    wait_k8s_resource_ready "deployment" "${DEPLOY_VARS["NFS_NAME"]}"
    success "NFS module is deployed."
}

uninstall_nfs() {

    delete_k8s_resource_by_file "${CONFIG["NFS_FILE"]}"
    success "NFS module is uninstalled."
}

render_nfs_sc_files() {
    # NFS_SHARE_PATH 为空（= 导出根 fsid=0 本身）时，provisioner 的挂载路径
    # 按惯例补为 "/"（nfs-sc 只单独启动，该赋值不影响其他模块）
    if [ -z "${DEPLOY_VARS["NFS_SHARE_PATH"]}" ]; then
        DEPLOY_VARS["NFS_SHARE_PATH"]="/"
    fi

    render_config_template ${CONFIG["NFS_SC_TEMPLATE_FILE"]} ${CONFIG["NFS_SC_FILE"]} "DEPLOY_VARS"
    success "NFS-SC module is rendered."
}

deploy_nfs_sc() {

    exec_cmd kubectl apply -f ${CONFIG["NFS_SC_FILE"]}
    wait_k8s_resource_ready "deployment" "${DEPLOY_VARS["NFS_SC_DNAME"]}"
    success "NFS-SC module is deployed."
}

uninstall_nfs_sc() {

    delete_k8s_resource_by_file "${CONFIG["NFS_SC_FILE"]}"
    success "NFS-SC module is uninstalled."
}

