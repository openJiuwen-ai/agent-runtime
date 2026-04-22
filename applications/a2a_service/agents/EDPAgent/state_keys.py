"""
Session state key 常量。

所有 key 集中在此定义，防止跨模块冲突。
"""

# 规则 4：总迭代计数
ITER_COUNT = "edp_iter_count"

# 规则 5：按工具名的执行计数
EXEC_COUNTS = "edp_exec_counts"

# HITL 输入尝试次数（按 tool_call_id 维度）
INPUT_ATTEMPTS = "edp_input_attempts"

# VA 委托（VersatileInterruptRail 使用）
PENDING_DELEGATE = "pending_delegate"
CASCADE_RESULT = "cascade_result"

# 用户终止标志
TERMINATION_TRIGGERED = "edp_termination_triggered"
