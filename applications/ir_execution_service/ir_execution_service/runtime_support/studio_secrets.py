# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""对 openjiuwen_studio SecurityUtils 的薄封装，用于环境变量与 IR 中密文字段解密。"""

from __future__ import annotations

import os

from openjiuwen_studio.core.manager.model_manager.utils.security_utils import SecurityUtils
from openjiuwen_runtime.foundation.log import get_logger

_log = get_logger(__name__)


def decrypt_optional_secret(raw: str) -> str:
    """按 Studio 规则解密一段字符串；未配置根密钥或为明文时原样返回。"""
    if not raw:
        return raw
    su = SecurityUtils()
    out = su.decrypt_api_key(raw)
    return out if out is not None else raw


def resolve_secret_env(env_key: str, default: str = "") -> str:
    """读环境变量名 env_key 的值并解密。

    若 HUAWEICLOUD_KMS_ENABLED 为 true，使用 SecurityUtils.get_decrypted_secret。
    否则在去掉首尾引号后，用 decrypt_optional_secret 处理（本地 AES 根密钥与 Studio 一致）。
    """
    use_kms = os.getenv("HUAWEICLOUD_KMS_ENABLED", "false").lower() == "true"
    if use_kms:
        fail_fast = os.getenv("SECRET_DECRYPT_FAIL_FAST", "false").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        try:
            val = SecurityUtils.get_decrypted_secret(env_key, default)
            return val if val is not None else default
        except Exception as e:
            if fail_fast:
                raise
            _log.warning(
                "KMS decrypt failed for env %s, falling back to default: %s", env_key, e
            )
            return default if default is not None else ""

    raw = os.getenv(env_key, default)
    if not raw:
        return default if default is not None else ""

    v = raw.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        v = v[1:-1].strip()
    return decrypt_optional_secret(v)
