"""Agent 身份的稳定 ID 生成。

``agent_id`` 由 ``runtime_template_id`` 与 ``agent_template_id`` 可逆编码得到：

1. 两个模板 id 都是 UUID，去掉连字符后各 16 字节；``runtime_template_id`` 为空时
   用 16 个 0x00 占位。
2. 拼接成 32 字节（256 bit）。
3. 按 base62（``0-9A-Za-z``）编码为固定 43 位。

编码是单射：不同模板对不会映射到同一 ID，因此不需要碰撞兜底；同输入恒等，
不依赖随机数、时间和数据库状态。字符集不含 ``-`` / ``_`` 等分隔符。

``agent_name`` 仍然由 ``agent_id`` 加 ``agent_`` 前缀得到。
"""

from __future__ import annotations

_BASE62 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
_UUID_BYTES = 16
AGENT_ID_LENGTH = 43


def _template_bytes(value: str) -> bytes:
    """把 UUID 形式的模板 id 转成 16 字节；非 UUID 抛 ``ValueError``。"""
    text = str(value or "").strip().lower().replace("-", "")
    if len(text) != _UUID_BYTES * 2:
        raise ValueError(f"template id is not a uuid: {value!r}")
    return bytes.fromhex(text)


def _encode_base62(payload: bytes) -> str:
    number = int.from_bytes(payload, "big")
    digits: list[str] = []
    for _ in range(AGENT_ID_LENGTH):
        number, remainder = divmod(number, len(_BASE62))
        digits.append(_BASE62[remainder])
    if number:
        raise ValueError("payload too large for agent id length")
    return "".join(reversed(digits))


def build_agent_id(runtime_template_id: str, agent_template_id: str) -> str:
    """由两个模板 id 计算 43 位 agent_id；模板 id 不是 UUID 时抛 ``ValueError``。"""
    runtime = str(runtime_template_id or "").strip()
    runtime_bytes = _template_bytes(runtime) if runtime else bytes(_UUID_BYTES)
    return _encode_base62(runtime_bytes + _template_bytes(agent_template_id))


def build_agent_name(agent_id: str) -> str:
    return f"agent_{agent_id}"


def try_build_agent_identity(
    runtime_template_id: str, agent_template_id: str
) -> tuple[str, str] | None:
    """返回 ``(agent_id, agent_name)``；缺少可用模板 id 时返回 ``None``。"""
    if not str(agent_template_id or "").strip():
        return None
    try:
        agent_id = build_agent_id(runtime_template_id, agent_template_id)
    except ValueError:
        return None
    return agent_id, build_agent_name(agent_id)
