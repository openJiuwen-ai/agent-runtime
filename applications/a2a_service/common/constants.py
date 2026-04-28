# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""
Redis Key 模板函数。
"""
from __future__ import annotations


def session_request_key(conv_id: str) -> str:
    """首轮请求的请求头和请求体缓存。"""
    return f"session:{conv_id}:request"
