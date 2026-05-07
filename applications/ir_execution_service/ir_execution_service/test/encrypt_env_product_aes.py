#!/usr/bin/env python3
# coding: utf-8
"""将 .env 中若干敏感项改为 SecurityUtils AES-GCM 密文，并切换为 SERVICE_MODE=product。

用法（在 applications/ir_execution_service 目录）：
  uv run python test/encrypt_env_product_aes.py

会先备份 .env 为 .env.bak.before_product_encrypt，再原地改写。
根密钥仅写入 SERVER_AES_MASTER_KEY_ENV（32 字节随机数的 Base64），并清空 SERVER_AES_MASTER_KEY。
控制台只打印根密钥 Base64 供你留存，不打印各业务明文。
"""

from __future__ import annotations

import base64
import logging
import os
import re
import shutil
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_SERVICE_ROOT = Path(__file__).resolve().parent.parent.parent


class _StdoutStderrHandler(logging.Handler):
    """INFO 走 stdout（等同原先无 file= 的 print），ERROR 走 stderr。"""

    terminator = "\n"

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            stream = sys.stderr if record.levelno >= logging.ERROR else sys.stdout
            stream.write(msg + self.terminator)
            self.flush()
        except RecursionError:
            raise
        except Exception:
            self.handleError(record)

# 与 ir 应用 resolve_secret_env / decrypt 路径一致
_SECRET_KEYS = (
    # "DB_PASSWORD",
    # "REDIS_PASSWORD",
    # "MILVUS_TOKEN",
    # "MILVUS_PASSWORD",
    # "DEFAULT_LLM_API_KEY",
    # "EMBED_API_KEY",
    # "OBS_ACCESS_KEY_ID",
    # "OBS_SECRET_ACCESS_KEY",
    "TAVILY_API_KEY"
)


def _load_plain_map(env_path: Path) -> dict[str, str]:
    try:
        from dotenv import dotenv_values
    except ImportError as e:
        raise ImportError("需要 python-dotenv：uv sync") from e

    raw = dotenv_values(env_path)
    out: dict[str, str] = {}
    for k, v in raw.items():
        if v is None or not str(v).strip():
            continue
        if k in _SECRET_KEYS or k.startswith("LLM_KEY__"):
            out[k] = str(v).strip().strip('"').strip("'")
    return out


def _rewrite_env_file(
    env_path: Path,
    replacements: dict[str, str],
) -> None:
    text = env_path.read_text(encoding="utf-8")
    raw_lines = text.splitlines(keepends=True)
    new_lines: list[str] = []
    for line in raw_lines:
        core = line.rstrip("\r\n")
        m = re.match(r"^(\s*)([A-Za-z_][A-Za-z0-9_]*)\s*=\s*", core)
        if not m:
            new_lines.append(core + "\n")
            continue
        key = m.group(2)
        if key in replacements:
            val = replacements[key]
            new_lines.append(f"{key}={val}\n")
            continue
        new_lines.append(core + "\n")

    body = "".join(new_lines)
    env_path.write_text(body, encoding="utf-8")


def main() -> int:
    logger.handlers.clear()
    logger.setLevel(logging.INFO)
    logger.propagate = False
    _h = _StdoutStderrHandler()
    _h.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(_h)

    env_path = _SERVICE_ROOT / ".env"
    if not env_path.is_file():
        logger.error("未找到 %s", env_path)
        return 1

    try:
        # Load .env values into process env so SecurityUtils can pick up master key.
        from dotenv import dotenv_values

        env_map = dotenv_values(env_path)
        for kk, vv in env_map.items():
            if vv is not None:
                os.environ[str(kk)] = str(vv)

        plain = _load_plain_map(env_path)
    except ImportError as e:
        logger.error("%s", e)
        return 1

    if not plain:
        logger.error("未发现需要加密的非空敏感项，退出。")
        return 1

    bak = _SERVICE_ROOT / ".env.bak.before_product_encrypt"
    shutil.copy2(env_path, bak)
    logger.info("已备份: %s", bak)

    from openjiuwen_studio.core.manager.model_manager.utils.security_utils import SecurityUtils

    su = SecurityUtils()
    if not su.get_initialized_master_key():
        logger.error("未配置可用的 SERVER_AES_MASTER_KEY_ENV（或 KMS 根密钥），无法加密。")
        return 1
    enc: dict[str, str] = {}
    for k, v in plain.items():
        c = su.encrypt_api_key(v)
        if c is None:
            continue
        enc[k] = c

    _rewrite_env_file(env_path, enc)

    # 校验：用新文件再解密一轮（不依赖进程里旧环境）
    from dotenv import dotenv_values

    check = dotenv_values(env_path)
    for kk, vv in check.items():
        if vv is not None:
            os.environ[kk] = str(vv)
    su2 = SecurityUtils()
    for k, v in enc.items():
        d = su2.decrypt_api_key(v)
        if d != plain[k]:
            logger.error("自检失败: %s", k)
            return 1

    logger.info("自检通过：密文可用当前根密钥解密。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
