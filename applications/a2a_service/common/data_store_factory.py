# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""运行时状态存储工厂：从 foundation 层导入，保持兼容。"""
from openjiuwen_runtime.foundation.state.data_store_factory import build_runtime_state_store_and_db_handler  # noqa: F401

__all__ = ["build_runtime_state_store_and_db_handler"]
