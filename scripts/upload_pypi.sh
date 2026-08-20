pip config set global.index-url https://repo.huaweicloud.com/repository/pypi/simple
pip config set global.trusted-host repo.huaweicloud.com
pip install uv==0.8.13 -i https://repo.huaweicloud.com/repository/pypi/simple

# 强制卸载旧版本
pip uninstall -y setuptools twine -y

# 安装依赖
pip install setuptools==82.0.1 wheel==0.46.3
pip install twine==6.2.0

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# 默认全部构建目录
DEFAULT_PKG_DIRS=(
    foundation
    management
    service
    server
)

# 如果传入参数，则使用传入的目录
if [ $# -gt 0 ]; then
    PKG_DIRS=("$@")
else
    PKG_DIRS=("${DEFAULT_PKG_DIRS[@]}")
fi

for pkg_dir in "${PKG_DIRS[@]}"
do
    cd "${PROJECT_DIR}/${pkg_dir}"

    # 修改自身版本号
    sed -i 's/^version = .*/version = "'"${VERSION}"'"/' pyproject.toml

    # 修改内部依赖版本号
    case "${pkg_dir}" in
        management|service)
            sed -i 's/"openjiuwen-runtime-foundation.*"/"openjiuwen-runtime-foundation=='"${VERSION}"'"/' pyproject.toml
            ;;
        server)
            sed -i 's/"openjiuwen-runtime-foundation.*"/"openjiuwen-runtime-foundation=='"${VERSION}"'"/' pyproject.toml
            sed -i 's/"openjiuwen-runtime-management.*"/"openjiuwen-runtime-management=='"${VERSION}"'"/' pyproject.toml
            ;;
        *)
            ;;
    esac

    # 构建（去私有源拉包）
    uv sync \
        --trusted-host devrepo.devcloud.cn-north-4.huaweicloud.com \
        -i "https://${USERNAME}:${PASSWORD}@${HUAWEI_PRIVATE_PYPI}/simple/" \
        --index-strategy unsafe-best-match

    uv build --out-dir dist 

    # 上传私有仓库
    twine upload \
        --repository-url "https://${HUAWEI_PRIVATE_PYPI}" \
        -u "${USERNAME}" \
        -p "${PASSWORD}" \
        dist/*

    # 官方 PyPI 上传
    echo "UPLOAD_PYPI=${UPLOAD_PYPI}"
    if [ "${UPLOAD_PYPI}" == "true" ]; then
        twine upload --verbose \
            --repository-url "${PYPI_REPOSITORY}" \
            --username __token__ \
            --password "${PYPI_TOKEN}" \
            dist/*
    fi
done
