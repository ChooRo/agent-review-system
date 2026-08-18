"""响应文件审查专用 Tool。"""

from typing import Any

from ..common.retrieval import search_blocks
from ..schemas import ToolContext


def get_procurement_requirement(
    *, context: ToolContext, requirements: list[dict[str, Any]], requirement_id: str
) -> dict[str, Any]:
    """作用：按稳定 ID 读取一条采购要求。
    输入：context、采购要求 requirements 和目标 requirement_id。
    输出：命中的要求字典；未命中时返回空字典。
    逻辑：顺序查找首个 item_id 相等的事项。
    """
    return next((item for item in requirements if item.get("item_id") == requirement_id), {})


def search_response_evidence(
    *, context: ToolContext, blocks: list[dict[str, Any]], requirement_text: str, top_k: int = 10
) -> dict[str, Any]:
    """作用：在当前供应商范围内检索响应文件证据。
    输入：带 supplier_id 的 context、已过滤 blocks、采购要求文本和 top_k。
    输出：关键词检索得到的候选证据 Block。
    逻辑：先强制校验供应商身份，再复用通用 Block 检索函数完成排序。
    """
    if not context.supplier_id:
        raise PermissionError("响应证据检索必须携带 supplier_id")
    return search_blocks(context=context, blocks=blocks, query=requirement_text, top_k=top_k)
