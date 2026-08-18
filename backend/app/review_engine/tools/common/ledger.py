"""业务台账查询 Tool。"""

from typing import Any

from ..schemas import ToolContext


def query_ledger_items(
    *, context: ToolContext, items: list[dict[str, Any]], category: str | None = None,
    item_ids: list[str] | None = None, page: int = 1, page_size: int = 50
) -> dict[str, Any]:
    """作用：按类别或事项 ID 分页查询业务台账。
    输入：context、台账 items、可选 category/item_ids，以及 page/page_size。
    输出：当前页事项、总数和分页参数组成的字典。
    逻辑：先校验分页范围，再依次应用类别和 ID 过滤，最后按页切片。
    """
    if page < 1 or not 1 <= page_size <= 200:
        raise ValueError("page 必须大于 0，page_size 必须在 1..200")
    allowed_ids = set(item_ids or [])
    selected = [
        item for item in items
        if (not category or item.get("category") == category)
        and (not allowed_ids or item.get("item_id") in allowed_ids)
    ]
    start = (page - 1) * page_size
    return {"items": selected[start:start + page_size], "total": len(selected), "page": page, "page_size": page_size}
