#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CLI_DIR="$PROJECT_DIR/cli"
DIST_DIR="${DIST_DIR:-$PROJECT_DIR/dist}"

FOUNDATION_TARGET="../foundation"
if [[ "${DB_TYPE:-}" =~ ^([Gg]auss[Dd]b|[Oo]pen[Gg]auss)$ ]]; then
    FOUNDATION_TARGET="../foundation[gaussdb]"
fi

# 清除可能指向其他 venv 的环境变量
unset VIRTUAL_ENV
export PYTHONPATH=""

# 加载全局配置：~/.openjiuwen/.env
GLOBAL_ENV="$HOME/.openjiuwen/.env"
if [ -f "$GLOBAL_ENV" ]; then
    echo "==> Loading config from $GLOBAL_ENV"
    while IFS='=' read -r key value; do
        key="$(echo "$key" | xargs)"
        [ -z "$key" ] && continue
        [[ "$key" == \#* ]] && continue
        value="$(echo "$value" | xargs)"
        export "$key=$value"
    done < "$GLOBAL_ENV"
fi

DIST_DIR="${DIST_DIR:-$PROJECT_DIR/dist}"

# ========== 1. 构建基础运行时 WHL ==========
echo "==> Building runtime base WHL packages..."
mkdir -p "$DIST_DIR"
rm -f "$DIST_DIR"/openjiuwen_runtime_*.whl

for pkg in foundation management service; do
    pkg_dir="$PROJECT_DIR/$pkg"
    if [ -d "$pkg_dir" ]; then
        echo "    Building $pkg..."
        uv build "$pkg_dir" --out-dir "$DIST_DIR"
    else
        echo "    Skipping $pkg (directory not found)"
    fi
done

# ========== 2. 构建 CLI 二进制 ==========
cd "$CLI_DIR"

echo "==> Setting up build venv..."
uv venv

echo "==> Installing CLI dependencies..."
uv sync

echo "==> Installing management package (editable)..."
uv pip install --python .venv/Scripts/python.exe -e ../management

echo "==> Installing foundation package (editable)..."
uv pip install --python .venv/Scripts/python.exe -e "$FOUNDATION_TARGET"

echo "==> Installing PyInstaller..."
uv pip install --python .venv/Scripts/python.exe pyinstaller

echo "==> Building standalone binary..."
.venv/Scripts/python.exe -m PyInstaller --onefile \
    --name openjiuwen \
    --distpath "$DIST_DIR" \
    --workpath build/ \
    --specpath build/ \
    --paths "$CLI_DIR" \
    --paths "$PROJECT_DIR/management" \
    --paths "$PROJECT_DIR/foundation" \
    --hidden-import openjiuwen_runtime \
    --hidden-import openjiuwen_runtime.cli \
    --hidden-import openjiuwen_runtime.cli.main \
    --hidden-import openjiuwen_runtime.cli.templates \
    --hidden-import openjiuwen_runtime.management \
    --hidden-import openjiuwen_runtime.management.manager \
    --hidden-import openjiuwen_runtime.management.models \
    --hidden-import openjiuwen_runtime.management.models.enums \
    --hidden-import openjiuwen_runtime.management.models.schemas \
    --hidden-import openjiuwen_runtime.management.models.deployment_params \
    --hidden-import openjiuwen_runtime.management.deployments \
    --hidden-import openjiuwen_runtime.management.deployments.base \
    --hidden-import openjiuwen_runtime.management.deployments.base.deployer \
    --hidden-import openjiuwen_runtime.management.deployments.base.models \
    --hidden-import openjiuwen_runtime.management.deployments.base.strategy \
    --hidden-import openjiuwen_runtime.management.deployments.subprocess \
    --hidden-import openjiuwen_runtime.management.deployments.subprocess.deployer \
    --hidden-import openjiuwen_runtime.management.deployments.subprocess.models \
    --hidden-import openjiuwen_runtime.management.deployments.subprocess.strategy \
    --hidden-import openjiuwen_runtime.management.deployments.docker \
    --hidden-import openjiuwen_runtime.management.deployments.docker.deployer \
    --hidden-import openjiuwen_runtime.management.deployments.docker.models \
    --hidden-import openjiuwen_runtime.management.deployments.docker.strategy \
    --hidden-import openjiuwen_runtime.management.deployments.k8s \
    --hidden-import openjiuwen_runtime.management.deployments.k8s.deployer \
    --hidden-import openjiuwen_runtime.management.deployments.k8s.models \
    --hidden-import openjiuwen_runtime.management.deployments.k8s.strategy \
    --hidden-import openjiuwen_runtime.foundation \
    --hidden-import openjiuwen_runtime.foundation.config \
    --hidden-import openjiuwen_runtime.foundation.packaging \
    --hidden-import openjiuwen_runtime.foundation.port_utils \
    --hidden-import openjiuwen_runtime.foundation.venv_manager \
    --hidden-import openjiuwen_runtime.foundation.docker_utils \
    --hidden-import openjiuwen_runtime.foundation.db \
    --hidden-import openjiuwen_runtime.foundation.db.handler \
    --hidden-import openjiuwen_runtime.foundation.db.sqlite_handler \
    --hidden-import openjiuwen_runtime.foundation.db.mysql_handler \
    --hidden-import openjiuwen_runtime.foundation.db.gaussdb_handler \
    --hidden-import openjiuwen_runtime.foundation.db.sqlalchemy_handler \
    --hidden-import openjiuwen_runtime.foundation.db.redis_handler \
    --hidden-import openjiuwen_runtime.foundation.db.table_def \
    --hidden-import openjiuwen_runtime.foundation.log \
    --hidden-import openjiuwen_runtime.foundation.log.config \
    --hidden-import openjiuwen_runtime.foundation.log.handler \
    --hidden-import openjiuwen_runtime.foundation.log.utils \
    --hidden-import aiosqlite \
    --hidden-import aiomysql \
    openjiuwen_runtime/cli/main.py

# ========== 3. 验证 ==========
BINARY="$DIST_DIR/openjiuwen.exe"
if [ -f "$BINARY" ]; then
    SIZE=$(du -h "$BINARY" | cut -f1)
    echo ""
    echo "=========================================="
    echo "Build succeeded!"
    echo "=========================================="
    echo "Binary:    $BINARY ($SIZE)"
    echo ""
    echo "Runtime base WHL packages:"
    ls "$DIST_DIR"/openjiuwen_runtime_*.whl 2>/dev/null | while read f; do echo "  $(basename "$f")"; done
    echo ""
    "$BINARY" --help
else
    echo "ERROR: Binary not found at $BINARY" >&2
    exit 1
fi
