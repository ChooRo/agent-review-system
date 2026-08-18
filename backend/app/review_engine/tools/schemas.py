"""Tool 调用的公共上下文和返回结构。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class ToolContext:
    """由后端注入的身份与权限；不得由大模型生成。"""

    run_id: str
    project_id: str = "mvp"
    task_id: str = "mvp"
    user_id: str = "system"
    agent: str = "workflow"
    permission_scope: tuple[str, ...] = ()
    supplier_id: str | None = None


@dataclass
class ToolResult:
    """统一 Tool 返回包，便于日志和后续 API 保持同一契约。"""

    tool_call_id: str
    status: str
    data: Any = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    duration_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        """作用：把工具返回对象转换为可序列化字典。
        输入：当前 ToolResult 实例。
        输出：包含全部数据类字段的字典。
        逻辑：使用 dataclasses.asdict 递归转换数据类字段。
        """
        return asdict(self)
