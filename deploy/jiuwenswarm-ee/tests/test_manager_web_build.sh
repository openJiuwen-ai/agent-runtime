#!/usr/bin/env bash
# Run with: bash deploy/jiuwenswarm-ee/tests/test_manager_web_build.sh
# No Kubernetes, Node.js, yq, or real deployment is needed.
set -euo pipefail

deploy_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
source "${deploy_dir}/common.sh"
source "${deploy_dir}/template_handler.sh"

test_dir=$(mktemp -d)
trap 'rm -rf "${test_dir}"' EXIT
repo="${test_dir}/repository with spaces"
dist="${repo}/applications/manager/manager_web/dist"
mkdir -p "${dist}/assets"

expect_failure() {
    local expected="$1" output
    shift
    if output=$(require_manager_web_build "$@" 2>&1); then
        echo "Expected build validation to fail: ${expected}" >&2
        exit 1
    fi
    [[ "${output}" == *"${expected}"* ]] || {
        echo "Unexpected error: ${output}" >&2
        exit 1
    }
}

expect_failure "existing absolute RUNTIME_CODE_PATH" ""
expect_failure "existing absolute RUNTIME_CODE_PATH" "relative/repo"
expect_failure "npm ci && npm run build" "${repo}"
touch "${dist}/index.html"
expect_failure "build is missing" "${repo}"

printf '<script type="module" src="/src/main.tsx"></script>\n' > "${dist}/index.html"
expect_failure "no built JavaScript entry" "${repo}"

cat > "${dist}/index.html" <<'HTML'
<!doctype html>
<script type="module" src="/assets/index-test.js"></script>
<link rel="stylesheet" href="/assets/index-test.css">
HTML
expect_failure "missing /assets/index-test.js" "${repo}"
printf 'console.log("test");\n' > "${dist}/assets/index-test.js"
expect_failure "missing /assets/index-test.css" "${repo}"
printf 'body { color: black; }\n' > "${dist}/assets/index-test.css"
require_manager_web_build "${repo}"

# A query/fragment is not part of the built filename; single quotes work too.
printf "<script src='/assets/index-test.js?v=1#module'></script>\n" > "${dist}/index.html"
require_manager_web_build "${repo}"

echo "Manager Web build validation: 9 cases passed"
