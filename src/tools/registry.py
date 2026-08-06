"""小型 Tool 注册表：白名单、调用和审计只实现一次。"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from typing import Any

from .schemas import ToolContext, ToolResult


ToolFunction = Callable[..., Any]


class ToolRegistry:
    """保存 Tool 实现，并拒绝 Agent 调用未授权能力。"""

    def __init__(self, tools: dict[str, ToolFunction], allowlists: dict[str, set[str]], event=None):
        self.tools = tools
        self.allowlists = allowlists
        self.event = event

    def call(self, name: str, context: ToolContext, **arguments: Any) -> ToolResult:
        """校验白名单、执行 Tool，并写入不包含完整正文的审计事件。"""
        call_id = f"TC-{uuid.uuid4().hex[:12]}"
        started = time.perf_counter()
        try:
            if name not in self.allowlists.get(context.agent, set()):
                raise PermissionError(f"Agent {context.agent} 无权调用 Tool：{name}")
            function = self.tools.get(name)
            if function is None:
                raise KeyError(f"Tool 未注册：{name}")
            data = function(context=context, **arguments)
            result = ToolResult(call_id, "success", data, duration_ms=int((time.perf_counter() - started) * 1000))
            self._audit(name, context, result, list(arguments))
            return result
        except Exception as exc:
            result = ToolResult(
                call_id,
                "rejected" if isinstance(exc, PermissionError) else "error",
                {"error_type": type(exc).__name__, "message": str(exc)},
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
            self._audit(name, context, result, list(arguments))
            return result

    def _audit(self, name: str, context: ToolContext, result: ToolResult, argument_names: list[str]) -> None:
        if self.event:
            self.event(
                "INFO" if result.status == "success" else "WARNING",
                "tool",
                "tool_called",
                f"{name}: {result.status}",
                tool_call_id=result.tool_call_id,
                tool=name,
                agent=context.agent,
                argument_names=argument_names,
                duration_ms=result.duration_ms,
            )
