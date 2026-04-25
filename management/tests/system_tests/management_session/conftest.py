# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

from __future__ import annotations

import sys
from pathlib import Path

_root = Path(__file__).resolve().parents[3]
_foundation = _root.parent / "foundation"
for p in (str(_root), str(_foundation)):
    if p not in sys.path:
        sys.path.insert(0, p)
