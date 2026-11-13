# coding: utf-8
"""Pod 进程兼容垫片（经 PYTHONPATH 自动加载，见 spec.py runtime 块）。

轻量单机形态适配：新版 jiuwenswarm（0.2.8 b1e201a8 起）的
``runtime.checkpointer._enterprise_db_client`` 在企业模式下硬性要求
MySQL/PostgreSQL 共享检查点库（"refusing to fall back to local SQLite"），
而轻量版无外部 DB——旧版 workswarm 在此场景是静默回退工作区本地 sqlite。

此垫片把该函数恢复为旧版语义（企业模式直接返回 None → 上层走
``db_type=sqlite`` / ``workspace/checkpoint`` 本地检查点）。仅影响
把本目录放进 PYTHONPATH 的进程（runtime 服务与 agentserver pod）；
runtime 自身不使用 checkpointer，补丁对其无副作用。

上游若提供正式的"企业模式允许本地检查点"开关（env/config），删除本垫片
并改用官方开关即可。
"""

from __future__ import annotations

import logging


async def _lite_enterprise_db_client():
    """旧版语义：企业模式不做共享库检查，交给上层本地 sqlite 回退。"""
    return None


try:
    import jiuwenswarm.runtime.checkpointer as _checkpointer

    if getattr(_checkpointer, "_enterprise_db_client", None) is not None:
        _checkpointer._enterprise_db_client = _lite_enterprise_db_client
        logging.getLogger(__name__).warning(
            "[lite-compat] checkpointer: 已禁用企业共享库硬检查,"
            "回退工作区本地 sqlite（轻量单机形态,见 spec.py PYTHONPATH 垫片）")
except Exception:
    # jiuwenswarm 不在搜索路径（如 manager/identity 进程）或上游改名时静默跳过
    pass
