#!/usr/bin/env bash
# Exercise normal Manager startup against a file-backed fake Kubernetes API.
# Requires Bash 4+ and mikefarah/yq v4. Never connects to a real cluster.
set -euo pipefail
if [ "${BASH_VERSINFO[0]}" -lt 4 ]; then
  printf 'This deployment integration test requires Bash 4+.\n' >&2
  exit 1
fi
command -v yq >/dev/null
command -v python3 >/dev/null
SOURCE_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
CASE_DIR=$(mktemp -d)
trap 'rm -rf "$CASE_DIR"' EXIT
TOOL_DIR="$CASE_DIR/tool"
STATE_DIR="$CASE_DIR/state"
mkdir -p "$TOOL_DIR/templates" "$TOOL_DIR/conf" "$STATE_DIR"
cp "$SOURCE_DIR"/*.sh "$TOOL_DIR/"
printf 'NAMESPACE=unused\nMANAGER_SERVER_IMAGE=example/manager-server:v1\n' > "$TOOL_DIR/.env.custom"
cat > "$TOOL_DIR/conf/agentserver.json" <<'EOF'
{"type":"config_sync","rawdata":{"containers":[{"container_id":"main","image":"example/agentserver:v1"}],"templates":[{"template_id":"default","namespace":"demo","main_container_id":"main"}],"scopes":[]}}
EOF
for component in identity manager-server manager-web; do
  cat > "$TOOL_DIR/templates/$component.template.yaml" <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: jiuwenswarm-$component
  namespace: <<NAMESPACE>>
spec:
  replicas: 1
  template:
    metadata:
      labels: {app: jiuwenswarm-$component}
    spec:
      containers:
        - name: manager
          image: example/$component:v1
          env: [{name: EXISTING, value: keep}]
EOF
done

source "$TOOL_DIR/deploy.sh"
# deploy.sh installs its own cleanup handler; restore cleanup for the whole fixture.
trap 'rm -rf "$CASE_DIR"' EXIT
info() { :; }
success() { :; }
warning() { printf '%s\n' "$*" >&2; }
error() { printf '%s\n' "$*" >&2; exit 1; }
assert_yq() { yq -e "$1" "$2" >/dev/null; }

# Keep parsing, target selection, rendering, resource splitting and apply routing
# real. Replace only environment validation, network and workload waits.
check_vars() { :; }
check_dependency() { :; }
kctl_check_vars() { :; }
kctl_ensure_namespace() { :; }
resolve_builtin_svc_locations() { :; }
component_probe_code() { printf 200; }
ensure_workload() { :; }
wait_ready() { printf '%s\n' "$1" >> "$CASE_DIR/waited"; }
kctl_http() {
  local method="$1" path="$2" response="$3" body="${4:-}"
  # Shared dependency discovery has no resource selected yet.
  if [ -z "${KCTL_KIND:-}" ]; then printf 200; return 0; fi
  local state="$STATE_DIR/$KCTL_KIND-$KCTL_NAME.yaml"
  case "$method" in
    GET)
      if [ -f "$state" ]; then cp "$state" "$response"; printf 200; else printf 404; fi;;
    POST)
      if [[ "$path" != *'?dryRun=All' ]]; then
        cp "$body" "$state"
        printf 'create %s %s\n' "$KCTL_KIND" "$KCTL_NAME" >> "$CASE_DIR/writes"
      fi
      printf 201;;
    PUT)
      cp "$body" "$state"
      printf 'update %s %s\n' "$KCTL_KIND" "$KCTL_NAME" >> "$CASE_DIR/writes"
      printf 200;;
    *) error "Unexpected HTTP method: $method";;
  esac
}
kctl_patch_strategic() {
  local state="$STATE_DIR/$KCTL_KIND-$KCTL_NAME.yaml"
  printf '%s' "$2" > "$CASE_DIR/patch.json"
  yq -o=json "$state" > "$CASE_DIR/current.json"
  # Kubernetes strategic merge preserves unrelated fields and merges named lists.
  python3 - "$CASE_DIR/current.json" "$CASE_DIR/patch.json" "$state" <<'PY'
import json, sys
def merge(old, patch):
    if isinstance(old, dict) and isinstance(patch, dict):
        return {**old, **{k: merge(old.get(k), v) for k, v in patch.items()}}
    if isinstance(old, list) and isinstance(patch, list):
        remaining = {v['name']: v for v in patch}
        result = [merge(v, remaining.pop(v['name'], {})) for v in old]
        return result + list(remaining.values())
    return patch
with open(sys.argv[1]) as f: old = json.load(f)
with open(sys.argv[2]) as f: patch = json.load(f)
with open(sys.argv[3], 'w') as f: json.dump(merge(old, patch), f)
PY
  printf 'update %s %s\n' "$KCTL_KIND" "$KCTL_NAME" >> "$CASE_DIR/writes"
  printf 200
}
run_startup() {
  # A fresh invocation resets CLI state; the fake cluster persists across runs.
  CMD= SCOPE= TARGETS=() TARGETS_EXPLICIT=0
  main "$@"
}

run_startup up -n demo manager
secret="$STATE_DIR/Secret-jiuwenswarm-manager-runtime-defaults.yaml"
deployment="$STATE_DIR/Deployment-jiuwenswarm-manager-server.yaml"
[ -f "$secret" ] || error 'normal Manager startup did not deliver its defaults Secret'
assert_yq '.metadata.namespace == "demo"' "$secret"
assert_yq '.spec.template.spec.volumes[0].secret.secretName == "jiuwenswarm-manager-runtime-defaults"' "$deployment"
assert_yq '.spec.template.spec.containers[0].volumeMounts[0].readOnly == true' "$deployment"
assert_yq '.spec.template.spec.containers[0].env[] | select(.name == "AGENTSERVER_JSON_PATH") | .value == "/etc/manager/runtime-defaults/agentserver.json"' "$deployment"
assert_yq '.spec.template.spec.containers[0].env[] | select(.name == "MANAGER_RUNTIME_DEFAULTS_ENABLED") | .value == "true"' "$deployment"
assert_yq '.spec.template.spec.containers[0].env[] | select(.name == "MANAGER_DEPLOY_ENV_JSON") | .value | from_json | .MANAGER_SERVER_IMAGE == "example/manager-server:v1"' "$deployment"
assert_yq '.spec.template.metadata.annotations."jiuwenswarm.io/runtime-defaults-sha256" == load("'"$secret"'").metadata.annotations."jiuwenswarm.io/runtime-defaults-sha256"' "$deployment"
yq -r '.data."agentserver.json" | @base64d' "$secret" > "$CASE_DIR/decoded.json"
diff <(yq -p=json -o=json -I=0 '.' "$TOOL_DIR/conf/agentserver.json") <(yq -p=json -o=json -I=0 '.' "$CASE_DIR/decoded.json")
python3 - "$CASE_DIR/writes" <<'PY'
import sys
lines = open(sys.argv[1]).read().splitlines()
assert lines.index('create Secret jiuwenswarm-manager-runtime-defaults') < lines.index('create Deployment jiuwenswarm-manager-server')
PY

# The existing-deployment path must add the handoff even for older deployments.
yq -i 'del(.spec.template.metadata.annotations) | del(.spec.template.spec.volumes) | del(.spec.template.spec.containers[0].volumeMounts) | .spec.template.spec.containers[0].env = [{"name":"EXISTING","value":"keep"}] | .spec.replicas = 3 | .spec.template.spec.containers[0].image = "example/custom:v9"' "$deployment"
: > "$CASE_DIR/writes"
run_startup up -n demo manager-server
assert_yq '.spec.template.spec.containers[0].env | length == 3' "$deployment"
assert_yq '.spec.replicas == 3 and .spec.template.spec.containers[0].image == "example/custom:v9"' "$deployment"
grep -q '^update Deployment jiuwenswarm-manager-server$' "$CASE_DIR/writes"

# Re-running the ordinary command refreshes changed source and keeps it idempotent.
yq -i -o=json '.rawdata.containers[0].image = "example/agentserver:v2"' "$TOOL_DIR/conf/agentserver.json"
: > "$CASE_DIR/writes"
run_startup up -n demo manager
grep -q '^update Secret jiuwenswarm-manager-runtime-defaults$' "$CASE_DIR/writes"
grep -q '^update Deployment jiuwenswarm-manager-server$' "$CASE_DIR/writes"
yq -r '.data."agentserver.json" | @base64d' "$secret" > "$CASE_DIR/decoded.json"
assert_yq '.rawdata.containers[0].image == "example/agentserver:v2"' "$CASE_DIR/decoded.json"
assert_yq '.spec.template.metadata.annotations."jiuwenswarm.io/runtime-defaults-sha256" == load("'"$secret"'").metadata.annotations."jiuwenswarm.io/runtime-defaults-sha256"' "$deployment"
: > "$CASE_DIR/writes"
run_startup up -n demo manager
[ ! -s "$CASE_DIR/writes" ] || error 'unchanged normal startup mutated resources'
printf 'Manager normal-startup default-template integration tests passed.\n'
