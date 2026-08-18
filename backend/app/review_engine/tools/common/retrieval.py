"""Block 检索、上下文补齐和原文定位 Tool。"""

from __future__ import annotations

from typing import Any

from ..schemas import ToolContext


def search_blocks(
    *, context: ToolContext, blocks: list[dict[str, Any]], query: str,
    keywords: list[str] | None = None, top_k: int = 10
) -> dict[str, Any]:
    """作用：用确定性关键词计分检索文档 Block。
    输入：context、候选 blocks、query、可选 keywords 和 top_k。
    输出：按相关度降序排列并带 retrieval_score 的候选 Block。
    逻辑：统计查询词在每个 Block 中的出现次数，过滤零分项后排序截断。
    """
    if not 1 <= top_k <= 100:
        raise ValueError("top_k 必须在 1..100")
    terms = [term.lower() for term in [query, *(keywords or [])] if term]
    ranked = []
    for block in blocks:
        text = str(block.get("text") or "").lower()
        score = sum(text.count(term) for term in terms)
        if score:
            ranked.append((score, block))
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    return {"blocks": [{**block, "retrieval_score": score} for score, block in ranked[:top_k]]}


def get_block_context(
    *, context: ToolContext, blocks: list[dict[str, Any]], block_ids: list[str], adjacent: int = 1
) -> dict[str, Any]:
    """作用：为目标 Block 补齐相邻上下文。
    输入：context、完整 blocks、目标 block_ids 和相邻范围 adjacent。
    输出：主 Block ID、扩展 ID、扩展 Block 及拼接文本。
    逻辑：定位目标下标，合并前后指定范围并按原文顺序去重输出。
    """
    if adjacent not in range(0, 4):
        raise ValueError("adjacent 必须在 0..3")
    positions = {block.get("block_id"): index for index, block in enumerate(blocks)}
    indexes = {i for bid in block_ids if bid in positions for i in range(max(0, positions[bid] - adjacent), min(len(blocks), positions[bid] + adjacent + 1))}
    expanded = [blocks[i] for i in sorted(indexes)]
    return {
        "primary_block_ids": block_ids,
        "expanded_block_ids": [block.get("block_id") for block in expanded],
        "blocks": expanded,
        "assembled_text": "\n".join(str(block.get("text") or "") for block in expanded),
    }


def locate_source(*, context: ToolContext, blocks: list[dict[str, Any]], block_ids: list[str]) -> dict[str, Any]:
    """作用：把证据 Block ID 定位回文档原文。
    输入：context、完整 blocks 和待定位 block_ids。
    输出：每项证据的 Block ID、页码、坐标和最多 300 字摘录。
    逻辑：按目标 ID 集合过滤 Block，并提取展示定位所需字段。
    """
    wanted = set(block_ids)
    return {"evidence": [{
        "block_id": block.get("block_id"), "page_no": block.get("page_no"),
        "bbox": block.get("bbox"), "quote": str(block.get("text") or "")[:300],
    } for block in blocks if block.get("block_id") in wanted]}
