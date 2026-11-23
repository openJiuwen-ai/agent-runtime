"""模板操作的可国际化错误。"""

from __future__ import annotations


class TemplateInUseError(ValueError):
    """模板仍被引用，禁止删除或停用。

    ``code`` 由前端映射为本地化文案，``count`` 为引用数量。
    """

    def __init__(self, code: str, count: int, message: str | None = None) -> None:
        self.code = code
        self.count = int(count)
        super().__init__(message or f"{code}: {self.count}")
