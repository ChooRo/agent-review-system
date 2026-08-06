"""采购文件审查专用 Tool。"""

from typing import Any

from .schemas import ToolContext


def get_procurement_topic_view(*, context: ToolContext, topic_views: dict[str, Any]) -> dict[str, Any]:
    """读取采购事项主题视图。输入场景视图；输出其副本。"""
    return dict(topic_views)


def check_required_elements(
    *, context: ToolContext, topic_views: dict[str, Any], required: list[str]
) -> dict[str, Any]:
    """检查必备主题。输入已有视图和必备主题；输出缺项候选。"""
    missing = sorted(set(required) - set(topic_views))
    return {"missing": missing, "complete": not missing, "needs_human_confirmation": bool(missing)}
