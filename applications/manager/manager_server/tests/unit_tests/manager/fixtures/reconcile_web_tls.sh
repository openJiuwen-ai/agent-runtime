#!/usr/bin/env bash
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
set -eu
source "$1/kubectl.sh"
source "$1/deploy_handler.sh"
CONF_DIR="$2"
KCTL_NS=web-test
kctl_tmpdir() { echo "$CONF_DIR"; }
kctl_patch_strategic() { kctl_patch "$@"; }
kctl_patch() {
    local name="${1##*/}" path="${1%/*}"
    printf '%s' "$2" > "$CONF_DIR/${path##*/}-${name}.json"
    echo 200
}
reconcile_web_tls
reconcile_manager_web_probe
