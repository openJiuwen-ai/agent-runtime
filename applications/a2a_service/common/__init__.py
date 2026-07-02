# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Shared runtime helpers package.

Event models live with the concrete agent implementation under
``agents.EDPAgent.events``. Keep package initialization lightweight so imports
such as ``common.logger`` and ``common.redis_client`` do not pull legacy event
modules into the runtime path.
"""

__all__: list[str] = []
