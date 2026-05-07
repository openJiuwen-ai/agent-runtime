PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVER_DIR=${PROJECT_DIR}/server
ENV_FILE=${SERVER_DIR}/.env


function is_absolute_path {
    local path="$1"
    if [[ "$OSTYPE" == "msys" || "$OSTYPE" == "cygwin" || "$OS" == "Windows_NT" ]]; then
        # Windows absolute paths: C:\xxx D:\xxx /c/xxx /d/xxx
        if [[ "$path" =~ ^[A-Za-z]:\\ || "$path" =~ ^/[A-Za-z]/ ]]; then
            return 0
        fi
    else
        # Linux/macOS absolute paths: /xxx
        if [[ "$path" == /* ]]; then
            return 0
        fi
    fi
    return 1
}

cd ${PROJECT_DIR}
# git checkout develop
git submodule update --init --recursive
git submodule update --remote --recursive

if [ ! -f "${ENV_FILE}" ]; then
    echo "============================================================"
    echo "ERROR: Environment file not found!"
    echo "Please prepare ${ENV_FILE} first."
    echo "============================================================"
    exit 1
fi

echo "Loading environment variables from ${ENV_FILE}"
set -a                  # Automatically export all variables
source ${ENV_FILE}      # Load environment variables from file
set +a                  # Disable automatic export

FOUNDATION_INSTALL_TARGET="../foundation"
DB_TYPE_NORMALIZED="$(echo "${DB_TYPE:-}" | tr '[:upper:]' '[:lower:]')"
if [[ "${DB_TYPE_NORMALIZED}" == "gaussdb" || "${DB_TYPE_NORMALIZED}" == "opengauss" ]]; then
    FOUNDATION_INSTALL_TARGET="../foundation[gaussdb]"
    echo "Detected DB_TYPE=${DB_TYPE}; install foundation with [gaussdb] optional dependency."
fi

if [[ "$OSTYPE" == "darwin"* ]]; then
    SED_I_FLAG="-i ''"
else
    SED_I_FLAG="-i"
fi

sed ${SED_I_FLAG} '/"openjiuwen-studio==/d' ${PROJECT_DIR}/applications/lowcode_agent/pyproject.toml
sed ${SED_I_FLAG} '/"openjiuwen-runtime-service==/d' ${PROJECT_DIR}/applications/lowcode_agent/pyproject.toml
sed ${SED_I_FLAG} '/"openjiuwen-runtime-foundation==/d' ${PROJECT_DIR}/cli/pyproject.toml
sed ${SED_I_FLAG} '/"openjiuwen-runtime-management==/d' ${PROJECT_DIR}/cli/pyproject.toml
sed ${SED_I_FLAG} '/"openjiuwen-runtime-foundation==/d' ${PROJECT_DIR}/management/pyproject.toml
sed ${SED_I_FLAG} '/"openjiuwen-runtime-foundation==/d' ${PROJECT_DIR}/server/pyproject.toml
sed ${SED_I_FLAG} '/"openjiuwen-runtime-management==/d' ${PROJECT_DIR}/server/pyproject.toml
sed ${SED_I_FLAG} '/"openjiuwen-runtime-foundation==/d' ${PROJECT_DIR}/service/pyproject.toml

# Auto-resolve DIST_DIR:
# Relative path = based on PROJECT_DIR; Absolute path = keep unchanged
if is_absolute_path "${DIST_DIR}"; then
    FINAL_DIST_DIR="${DIST_DIR}"
else
    FINAL_DIST_DIR="${PROJECT_DIR}/${DIST_DIR}"
fi

echo "✅ Final build output directory (absolute path resolved): ${FINAL_DIST_DIR}"
rm -rf ${FINAL_DIST_DIR}
mkdir ${FINAL_DIST_DIR}

# complie dist/openjiuwen_studio-0.1.5-py3-none-any.whl
cd ${PROJECT_DIR}/agent-studio/backend
uv sync ${UV_EXTRA_ARGS}
rm -rf dist
uv build --out-dir ${FINAL_DIST_DIR} ${UV_EXTRA_ARGS}

# complie dist/openjiuwen-0.1.9-py3-none-any.whl (core library)
if [ -d "${PROJECT_DIR}/../agent-core" ]; then
    cd ${PROJECT_DIR}/../agent-core
    uv sync ${UV_EXTRA_ARGS}
    rm -rf dist
    uv build --out-dir ${FINAL_DIST_DIR} ${UV_EXTRA_ARGS}
fi

# complie dist/lowcode_agent_runner-0.1.0-py3-none-any.whl
cd ${PROJECT_DIR}/applications/lowcode_agent
uv sync ${UV_EXTRA_ARGS}
rm -rf dist
uv build --out-dir ${FINAL_DIST_DIR} ${UV_EXTRA_ARGS}

# complie dist/openjiuwen_runtime_service-0.1.0-py3-none-any.whl
cd ${PROJECT_DIR}/service
uv sync ${UV_EXTRA_ARGS}
rm -rf dist
uv build --out-dir ${FINAL_DIST_DIR} ${UV_EXTRA_ARGS}

# complie dist/openjiuwen_runtime_foundation-0.1.0-py3-none-any.whl
cd ${PROJECT_DIR}/foundation
uv sync ${UV_EXTRA_ARGS}
rm -rf dist
uv build --out-dir ${FINAL_DIST_DIR} ${UV_EXTRA_ARGS}

# Before run this, please prepare ${PROJECT_DIR}/server/.env
cd ${PROJECT_DIR}/server
uv sync ${UV_EXTRA_ARGS}

if [[ "$OSTYPE" == "msys" || "$OSTYPE" == "cygwin" || "$OS" == "Windows_NT" ]]; then
    # Windows (Git Bash / WSL / Cygwin)
    source .venv/Scripts/activate
else
    # Linux / macOS 
    source .venv/bin/activate
fi
uv pip install -e ../management ${UV_EXTRA_ARGS}
uv pip install -e "${FOUNDATION_INSTALL_TARGET}" ${UV_EXTRA_ARGS}

python -m openjiuwen_runtime.server.main 2>&1 | tee server.log
