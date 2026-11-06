#!/usr/bin/env bash
# Run with mikefarah/yq v4 on PATH. Uses synthetic files, never a live cluster.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
CASE_DIR=$(mktemp -d)
trap 'rm -rf "$CASE_DIR"' EXIT
info() { :; }
success() { :; }
warning() { printf '%s\n' "$*"; }
error() { printf '%s\n' "$*" >&2; exit 1; }
assert_yq() { yq -e "$1" "$2" >/dev/null; }

cat > "$CASE_DIR/agentserver.json" <<'EOF'
{"type":"config_sync","rawdata":{"containers":[{"container_id":"main","image":"example/agentserver:v1","env":{"TEST_SECRET":"synthetic-secret"},"envFrom":[{"secretRef":{"name":"runtime-env"}}]}],"templates":[{"template_id":"default","namespace":"demo","main_container_id":"main","volumes":[{"name":"data","nfs":{"server":"10.0.0.1","path":"/final-rendered"}}]}],"scopes":[]}}
EOF
cat > "$CASE_DIR/base.yaml" <<'EOF'
apiVersion: v1
kind: Service
metadata:
  name: manager
  namespace: demo
spec:
  ports: [{port: 8765}]
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: manager
  namespace: demo
spec:
  template:
    metadata:
      labels: {app: manager}
    spec:
      volumes:
        - name: code
          hostPath: {path: /existing/code}
      containers:
        - name: manager
          image: example/manager:v1
          env: [{name: EXISTING, value: keep}]
          volumeMounts: [{name: code, mountPath: /app/code}]
EOF

for variant in openshift jiuwenswarm-ee; do
  source "$ROOT/$variant/manager_defaults_handler.sh"
  manifest="$CASE_DIR/$variant.yaml"
  cp "$CASE_DIR/base.yaml" "$manifest"
  render_manager_runtime_defaults "$manifest" "$CASE_DIR/agentserver.json" demo
  assert_yq 'select(.kind == "Secret") | .metadata.namespace == "demo"' "$manifest"
  yq -r 'select(.kind == "Secret") | .data."agentserver.json" | @base64d' "$manifest" > "$CASE_DIR/decoded.json"
  diff <(yq -p=json -o=json -I=0 '.' "$CASE_DIR/agentserver.json") <(yq -p=json -o=json -I=0 '.' "$CASE_DIR/decoded.json")
  assert_yq 'select(.kind == "Deployment") | .spec.template.spec.volumes | length == 2' "$manifest"
  assert_yq 'select(.kind == "Deployment") | .spec.template.spec.containers[0].env | length == 3' "$manifest"
  assert_yq 'select(.kind == "Deployment") | .spec.template.spec.containers[0].volumeMounts[1].readOnly == true' "$manifest"
  assert_yq 'select(.kind == "Deployment") | .spec.template.spec.volumes[1].secret.defaultMode == 292' "$manifest"
  assert_yq 'select(.kind == "Service") | .spec.ports[0].port == 8765' "$manifest"
  cp "$manifest" "$CASE_DIR/once.yaml"
  render_manager_runtime_defaults "$manifest" "$CASE_DIR/agentserver.json" demo
  cmp "$manifest" "$CASE_DIR/once.yaml"
  dd if=/dev/zero of="$CASE_DIR/large.json" bs=181000 count=1 2>/dev/null
  if render_manager_runtime_defaults "$manifest" "$CASE_DIR/large.json" demo >/dev/null 2>&1; then
    error 'oversized payload was accepted'
  fi
  cmp "$manifest" "$CASE_DIR/once.yaml"
  if render_manager_runtime_defaults "$manifest" "$CASE_DIR/agentserver.json" another >/dev/null 2>&1; then
    error 'cross-namespace source was accepted'
  fi
  cmp "$manifest" "$CASE_DIR/once.yaml"
  printf '{invalid-json' > "$CASE_DIR/invalid.json"
  if render_manager_runtime_defaults "$manifest" "$CASE_DIR/invalid.json" demo >/dev/null 2>&1; then
    error 'invalid JSON was accepted'
  fi
  cmp "$manifest" "$CASE_DIR/once.yaml"
  cp "$CASE_DIR/base.yaml" "$CASE_DIR/missing.yaml"
  render_manager_runtime_defaults "$CASE_DIR/missing.yaml" "$CASE_DIR/missing.json" demo >/dev/null
  cmp "$CASE_DIR/base.yaml" "$CASE_DIR/missing.yaml"
  # Exercise both shipped manifest layouts (EE includes a second Service).
  sed -e 's/<<NAMESPACE>>/demo/g' -e 's/<<[^>]*>>/test-value/g' \
    "$ROOT/$variant/templates/manager-server.template.yaml" > "$CASE_DIR/shipped.yaml"
  render_manager_runtime_defaults "$CASE_DIR/shipped.yaml" "$CASE_DIR/agentserver.json" demo
  assert_yq 'select(.kind == "Deployment") | .spec.template.spec.containers[] | select(.name == "manager") | .env[] | select(.name == "AGENTSERVER_JSON_PATH") | .value == "/etc/manager/runtime-defaults/agentserver.json"' "$CASE_DIR/shipped.yaml"
done
cmp "$ROOT/openshift/manager_defaults_handler.sh" "$ROOT/jiuwenswarm-ee/manager_defaults_handler.sh"

# OpenShift update behavior: Secret uses file-backed PUT; Deployment patches only
# this feature's env/mount/checksum, preserving unrelated replica/image choices.
source "$ROOT/openshift/manager_defaults_handler.sh"
yq 'select(.kind == "Secret")' "$CASE_DIR/openshift.yaml" > "$CASE_DIR/secret-old.yaml"
yq 'select(.kind == "Deployment")' "$CASE_DIR/openshift.yaml" > "$CASE_DIR/deployment-old.yaml"
yq -p=json -o=json '.rawdata.containers[0].image = "example/agentserver:v2"' "$CASE_DIR/agentserver.json" > "$CASE_DIR/v2.json"
render_manager_runtime_defaults "$CASE_DIR/openshift.yaml" "$CASE_DIR/v2.json" demo
yq 'select(.kind == "Secret")' "$CASE_DIR/openshift.yaml" > "$CASE_DIR/secret-new.yaml"
yq 'select(.kind == "Deployment")' "$CASE_DIR/openshift.yaml" > "$CASE_DIR/deployment-new.yaml"
KCTL_KIND=Secret
KCTL_LK=secret
KCTL_NAME=jiuwenswarm-manager-runtime-defaults
KCTL_PATH=/api/v1/namespaces/demo/secrets
kctl_http() {
  [ "$1" = PUT ] || error 'expected PUT'
  cp "$4" "$CASE_DIR/put.yaml"
  printf 200
}
update_existing_manager_runtime_defaults "$CASE_DIR/secret-new.yaml" "$CASE_DIR/secret-old.yaml"
assert_yq '.data."agentserver.json" == load("'"$CASE_DIR/secret-new.yaml"'").data."agentserver.json"' "$CASE_DIR/put.yaml"
cp "$CASE_DIR/secret-new.yaml" "$CASE_DIR/secret-drift.yaml"
yq -i '.data."agentserver.json" = "dGFtcGVyZWQ="' "$CASE_DIR/secret-drift.yaml"
rm "$CASE_DIR/put.yaml"
update_existing_manager_runtime_defaults "$CASE_DIR/secret-new.yaml" "$CASE_DIR/secret-drift.yaml"
assert_yq '.data."agentserver.json" == load("'"$CASE_DIR/secret-new.yaml"'").data."agentserver.json"' "$CASE_DIR/put.yaml"
KCTL_KIND=Deployment
KCTL_LK=deployment
KCTL_NAME=manager
KCTL_PATH=/apis/apps/v1/namespaces/demo/deployments
kctl_patch_strategic() { printf '%s' "$2" > "$CASE_DIR/pod-patch.json"; printf 200; }
update_existing_manager_runtime_defaults "$CASE_DIR/deployment-new.yaml" "$CASE_DIR/deployment-old.yaml"
assert_yq '.spec.template.spec.containers[0].env | length == 2' "$CASE_DIR/pod-patch.json"
assert_yq '.spec.template.spec.volumes | length == 1' "$CASE_DIR/pod-patch.json"
assert_yq '.spec.template.spec.containers[0].image == null and .spec.replicas == null' "$CASE_DIR/pod-patch.json"
if is_manager_runtime_defaults_resource "$CASE_DIR/base.yaml" >/dev/null 2>&1; then
  error 'unrelated resources matched the default-template update policy'
fi
is_manager_runtime_defaults_resource "$CASE_DIR/deployment-new.yaml"
rm "$CASE_DIR/pod-patch.json"
update_existing_manager_runtime_defaults "$CASE_DIR/deployment-new.yaml" "$CASE_DIR/deployment-new.yaml"
[ ! -e "$CASE_DIR/pod-patch.json" ] || error 'unchanged payload restarted Manager'
printf 'Manager runtime-default handoff tests passed (OCP + EE).\n'
