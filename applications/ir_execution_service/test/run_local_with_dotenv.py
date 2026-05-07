# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""本地测试入口：加载服务根目录 .env，补齐 OBS 占位，再启动服务。

用法（在 applications/ir_execution_service 下）：
  uv run python test/run_local_with_dotenv.py

可选环境变量：IR_EXEC_HOST（默认 0.0.0.0）、IR_EXEC_PORT（默认 8090）。
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
)
_BOOT_LOG = logging.getLogger(__name__)

# 本文件在 .../ir_execution_service/test/；应用与 ir_execution_service_app 在上一级
_SERVICE_ROOT = Path(__file__).resolve().parent.parent
_TEST_IR_ROOT = Path(__file__).resolve().parent


def _ensure_env(log: logging.Logger) -> None:
    try:
        from dotenv import load_dotenv
    except ImportError as e:
        raise ImportError("缺少 python-dotenv，请在本目录执行: uv sync") from e

    for env_path in (_SERVICE_ROOT / ".env", _TEST_IR_ROOT / ".env"):
        if env_path.is_file():
            load_dotenv(env_path)
            break
    else:
        log.warning(
            "未找到 %s 或 %s，仅使用当前进程已有环境变量",
            _SERVICE_ROOT / ".env",
            _TEST_IR_ROOT / ".env",
        )

    _TEST_IR_ROOT.mkdir(parents=True, exist_ok=True)

    obs_placeholders = {
        "OBS_ACCESS_KEY_ID": "local-placeholder",
        "OBS_SECRET_ACCESS_KEY": "local-placeholder",
        "OBS_SERVER": "https://obs.local-placeholder.invalid",
        "OBS_REGION": "local",
        "LOWCODE_IR_OBS_BUCKET": "local-placeholder-bucket",
    }
    for key, val in obs_placeholders.items():
        if not (os.environ.get(key) or "").strip():
            os.environ[key] = val


def main() -> None:
    try:
        _ensure_env(_BOOT_LOG)
    except ImportError as e:
        _BOOT_LOG.error("%s", e)
        sys.exit(1)

    # 本脚本仅用于本地测试，优先使用标准库 logging，避免额外依赖与 import-time 副作用。
    # 注意：后续导入 ir_execution_service_app/openjiuwen_runtime.service 仍可能触发 foundation Settings 初始化，
    # 因此必须确保 dotenv 已加载（见上面的 _ensure_env）。
    log = logging.getLogger(__name__)

    service = str(_SERVICE_ROOT)
    if service not in sys.path:
        sys.path.append(service)

    import ir_execution_service_app as app_entry

    host = (os.environ.get("IR_EXEC_HOST") or "0.0.0.0").strip()
    port = int((os.environ.get("IR_EXEC_PORT") or "8090").strip())
    log.info(
        "IR 拉取：通过 OBS（可选二级缓存：内存/Redis）读取；本脚本会注入 OBS 占位配置以便本地启动。\n"
        "监听: http://%s:%s",
        host,
        port,
    )
    app_entry.runner.run(host=host, port=port)


if __name__ == "__main__":
    main()
