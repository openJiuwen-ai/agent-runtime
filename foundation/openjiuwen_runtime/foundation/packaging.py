# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

import os
import subprocess
import sys
import shutil
from pathlib import Path
from typing import Optional


async def package_python_to_whl(
    source_dir: str,
    output_dir: Optional[str] = None,
    package_name: Optional[str] = None,
) -> str:
    """
    将Python代码打包为whl文件
    
    Args:
        source_dir: 源代码目录
        output_dir: 输出目录，默认为source_dir/dist
        package_name: 包名，用于验证
        
    Returns:
        str: 生成的whl文件路径
    """
    source_path = Path(source_dir).resolve()
    if not source_path.exists():
        raise FileNotFoundError(f"Source directory not found: {source_dir}")

    if output_dir is None:
        output_path = source_path / "dist"
    else:
        output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    setup_py = source_path / "setup.py"
    pyproject_toml = source_path / "pyproject.toml"

    if not setup_py.exists() and not pyproject_toml.exists():
        raise ValueError(f"No setup.py or pyproject.toml found in {source_dir}")

    try:
        result = subprocess.run(
            [sys.executable, "-m", "build", "--wheel", "--outdir", str(output_path)],
            cwd=str(source_path),
            capture_output=True,
            text=True,
            timeout=600,
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"Build timed out after {e.timeout}s") from e

    if result.returncode != 0:
        raise RuntimeError(f"Build failed: {result.stderr}")

    whl_files = list(output_path.glob("*.whl"))
    if not whl_files:
        raise RuntimeError("No wheel file generated")

    if package_name:
        matching_files = [f for f in whl_files if f.name.startswith(package_name)]
        if matching_files:
            return str(matching_files[0])

    return str(whl_files[0])


def create_virtualenv(venv_path: str, python_version: Optional[str] = None) -> str:
    """
    创建Python虚拟环境
    
    Args:
        venv_path: 虚拟环境路径
        python_version: Python版本，如"3.10"
        
    Returns:
        str: 虚拟环境路径
    """
    venv_path = Path(venv_path)
    
    if venv_path.exists():
        shutil.rmtree(venv_path)

    cmd = [sys.executable, "-m", "venv", str(venv_path)]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"Failed to create virtualenv: timed out after {e.timeout}s") from e

    if result.returncode != 0:
        raise RuntimeError(f"Failed to create virtualenv: {result.stderr}")

    return str(venv_path)


def get_pip_path(venv_path: str) -> str:
    """获取虚拟环境中的pip路径"""
    venv_path = Path(venv_path)
    if os.name == "nt":
        return str(venv_path / "Scripts" / "pip.exe")
    return str(venv_path / "bin" / "pip")


def get_python_path(venv_path: str) -> str:
    """获取虚拟环境中的python路径"""
    venv_path = Path(venv_path)
    if os.name == "nt":
        return str(venv_path / "Scripts" / "python.exe")
    return str(venv_path / "bin" / "python")


def install_package(venv_path: str, package: str) -> bool:
    """
    在虚拟环境中安装包
    
    Args:
        venv_path: 虚拟环境路径
        package: 包名或whl文件路径
        
    Returns:
        bool: 安装是否成功
    """
    pip_path = get_pip_path(venv_path)
    
    try:
        result = subprocess.run(
            [pip_path, "install", package],
            capture_output=True,
            text=True,
            timeout=600,
        )
    except subprocess.TimeoutExpired:
        return False

    return result.returncode == 0


def uninstall_package(venv_path: str, package: str) -> bool:
    """
    在虚拟环境中卸载包
    
    Args:
        venv_path: 虚拟环境路径
        package: 包名
        
    Returns:
        bool: 卸载是否成功
    """
    pip_path = get_pip_path(venv_path)
    
    try:
        result = subprocess.run(
            [pip_path, "uninstall", "-y", package],
            capture_output=True,
            text=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        return False

    return result.returncode == 0
