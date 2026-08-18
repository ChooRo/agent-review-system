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
        """作用：初始化工具注册表。
        输入：tools 为工具名到函数的映射，allowlists 为 Agent 白名单，event 为可选审计回调。
        输出：无返回值，在实例中保存三项配置。
        逻辑：直接保存已完成校验和组装的注册信息，供后续调用复用。
        """
        self.tools = tools
        self.allowlists = allowlists
        self.event = event

    def call(self, name: str, context: ToolContext, **arguments: Any) -> ToolResult:
        """作用：安全调用指定工具并记录审计结果。
        输入：name 为工具名，context 为调用上下文，arguments 为工具业务参数。
        输出：包含状态、数据或错误信息及耗时的 ToolResult。
        逻辑：校验 Agent 白名单和工具注册状态，执行函数；成功或异常都写入审计事件。
        """
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
        """作用：向外部事件回调发送一次精简的工具调用记录。
        输入：工具名、调用上下文、调用结果和参数名称列表。
        输出：无返回值；配置回调时产生一条审计事件。
        逻辑：按结果状态选择日志级别，仅记录元数据而不记录完整业务正文。
        """
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
