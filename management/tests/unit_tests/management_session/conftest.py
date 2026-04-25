# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""将 management 与 foundation 源码根加入 path，保证 openjiuwen_runtime 命名空间可导入。"""

from __future__ import annotations

import sys
from pathlib import Path

_root = Path(__file__).resolve().parents[3]  # .../management
_foundation = _root.parent / "foundation"
for p in (str(_root), str(_foundation)):
    if p not in sys.path:
        sys.path.insert(0, p)
