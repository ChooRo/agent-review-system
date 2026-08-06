"""响应文件审查专用 Tool。"""

from typing import Any

from .retrieval_tools import search_blocks
from .schemas import ToolContext


def get_procurement_requirement(
    *, context: ToolContext, requirements: list[dict[str, Any]], requirement_id: str
) -> dict[str, Any]:
    """按 ID 读取采购要求。输入采购台账；输出单个要求。"""
    return next((item for item in requirements if item.get("item_id") == requirement_id), {})


def search_response_evidence(
    *, context: ToolContext, blocks: list[dict[str, Any]], requirement_text: str, top_k: int = 10
) -> dict[str, Any]:
    """仅在当前供应商范围内检索响应证据；供应商范围由后端先行过滤。"""
    if not context.supplier_id:
        raise PermissionError("响应证据检索必须携带 supplier_id")
    return search_blocks(context=context, blocks=blocks, query=requirement_text, top_k=top_k)
