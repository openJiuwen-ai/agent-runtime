#!/usr/bin/env bash
# Synthetic OBS handoff checks. No Kubernetes or object-storage calls are made.
set -euo pipefail
if [ "${BASH_VERSINFO[0]}" -lt 4 ]; then
  printf 'This deployment test requires Bash 4+.\n' >&2
  exit 1
fi
SOURCE_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
CASE_DIR=$(mktemp -d)
trap 'rm -rf "$CASE_DIR"' EXIT
source "$SOURCE_DIR/k8s_handler.sh"
declare -A DEPLOY_VARS=([NAMESPACE]=demo)
info() { :; }
success() { :; }
warning() { printf '%s\n' "$*" >> "$CASE_DIR/warnings"; }
assert_key() {
  local actual
  actual=$(_agentserver_default_object_key)
  [ "$actual" = "$1" ] || {
    printf 'Unexpected object key: %s (expected %s)\n' "$actual" "$1" >&2
    exit 1
  }
}

# Defaults, the ordinary populated deployment variable, and custom paths all
# must resolve to the same object key that Manager reads for this namespace.
assert_key 'defaults/demo/agentserver.json'
DEPLOY_VARS[AGENT_RUNTIME_DEFAULT_CONF_PATH]=''
assert_key 'defaults/demo/agentserver.json'
DEPLOY_VARS[AGENT_RUNTIME_DEFAULT_CONF_PATH]='defaults/{ns}/agentserver.json'
assert_key 'defaults/demo/agentserver.json'
DEPLOY_VARS[AGENT_RUNTIME_DEFAULT_CONF_PATH]='custom/{ns}/runtime/{ns}.json'
assert_key 'custom/demo/runtime/demo.json'
DEPLOY_VARS[AGENT_RUNTIME_DEFAULT_CONF_PATH]='fixed/runtime.json'
assert_key 'fixed/runtime.json'
DEPLOY_VARS[AGENT_RUNTIME_DEFAULT_CONF_PATH]='defaults/{ns}/agentserver.json'
unset 'DEPLOY_VARS[NAMESPACE]'
assert_key 'defaults/default/agentserver.json'
DEPLOY_VARS[NAMESPACE]=''
assert_key 'defaults/default/agentserver.json'
DEPLOY_VARS[NAMESPACE]=demo

CONF_DIR="$CASE_DIR/conf"
mkdir -p "$CONF_DIR"
printf '{"rawdata":{"templates":[{"namespace":"demo","nodeName":"final-node"}]}}\n' > "$CONF_DIR/agentserver.json"
DEPLOY_VARS[ENABLE_EXTERNAL_OBS]=true
DEPLOY_VARS[OBS_URL]='http://synthetic-obs:9000'
DEPLOY_VARS[OBS_BUCKET]='synthetic-bucket'
DEPLOY_VARS[OBS_ACCESS_KEY]='synthetic-access'
DEPLOY_VARS[OBS_SECRET_KEY]='synthetic-secret'

# Replace only transport; the real upload/delete helpers build their arguments.
python3() {
  local key='' file='' action=upload
  shift # uploader script
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --object-key) key="$2"; shift 2;;
      --file) file="$2"; shift 2;;
      --delete) action=delete; shift;;
      *) shift 2;;
    esac
  done
  printf '%s %s\n' "$action" "$key" >> "$CASE_DIR/requests"
  [ -z "$file" ] || cp "$file" "$CASE_DIR/uploaded.json"
}
upload_agentserver_default_config
grep -qx 'upload defaults/demo/agentserver.json' "$CASE_DIR/requests"
cmp "$CONF_DIR/agentserver.json" "$CASE_DIR/uploaded.json"
delete_agentserver_default_config
grep -qx 'delete defaults/demo/agentserver.json' "$CASE_DIR/requests"

DEPLOY_VARS[RENDER_ONLY]=true
cp "$CASE_DIR/requests" "$CASE_DIR/before-render-only"
upload_agentserver_default_config
cmp "$CASE_DIR/requests" "$CASE_DIR/before-render-only"
DEPLOY_VARS[RENDER_ONLY]=false

# Optional OBS failures must leave normal runtime deployment available.
python3() { return 1; }
upload_agentserver_default_config
grep -q '上传默认 agentserver.json 失败' "$CASE_DIR/warnings"
printf 'AgentServer default OBS handoff tests passed.\n'
