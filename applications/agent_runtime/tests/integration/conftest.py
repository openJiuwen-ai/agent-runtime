# coding: utf-8
"""integration 层 fixtures：导出双实例 harness 的 dual fixture。

pytest 只自动发现 conftest.py 里的 fixture；_dual_harness.py 是普通模块
（无 test_ 前缀不被收集），在此显式重导出供 test_multi_replica.py 使用。
"""

from tests.integration._dual_harness import dual  # noqa: F401
