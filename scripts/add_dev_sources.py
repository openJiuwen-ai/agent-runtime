import os
import tomlkit

# 项目根目录（脚本放在 scripts/ 下）
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)

# 默认要处理的目录
DEFAULT_PKG_DIRS = [
    "foundation",
    "management",
    "service",
    "server",
]

# 配置映射
PACKAGE_CONFIGS = {
    "management": {
        "openjiuwen-runtime-foundation": {"path": "../foundation", "editable": True}
    },
    "service": {
        "openjiuwen-runtime-foundation": {"path": "../foundation", "editable": True}
    },
    "server": {
        "openjiuwen-runtime-foundation": {"path": "../foundation", "editable": True},
        "openjiuwen-runtime-management": {"path": "../management", "editable": True},
    },
}

def process_pyproject(pkg_dir):
    full_path = os.path.join(PROJECT_DIR, pkg_dir)
    pyproject_path = os.path.join(full_path, "pyproject.toml")

    if not os.path.exists(pyproject_path):
        print(f"⚠️ {pkg_dir}: 无 pyproject.toml，跳过")
        return

    print(f"========================================")
    print(f"处理：{pkg_dir}")

    with open(pyproject_path, "r", encoding="utf-8") as f:
        doc = tomlkit.load(f)

    if pkg_dir in PACKAGE_CONFIGS:
        # ====================== 核心正确写法 ======================
        # 创建独立分段：[tool.uv.sources]
        sources_table = tomlkit.table()
        for key, value in PACKAGE_CONFIGS[pkg_dir].items():
            it = tomlkit.inline_table()
            it.update(value)
            sources_table[key] = it

        doc["tool"]["uv"]["sources"] = sources_table
        # ==========================================================

    # 输出统一 LF 换行
    content = tomlkit.dumps(doc).replace("\r\n", "\n")
    with open(pyproject_path, "w", encoding="utf-8", newline="") as f:
        f.write(content)

    print(f"✅ 完成：{pkg_dir}")

def main():
    for pkg_dir in DEFAULT_PKG_DIRS:
        process_pyproject(pkg_dir)

    print("\n🎉 所有项目配置完成！")

if __name__ == "__main__":
    main()