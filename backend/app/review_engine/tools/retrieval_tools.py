"""Block 检索、上下文补齐和原文定位 Tool。"""

from __future__ import annotations

from typing import Any

from .schemas import ToolContext


def search_blocks(
    *, context: ToolContext, blocks: list[dict[str, Any]], query: str,
    keywords: list[str] | None = None, top_k: int = 10
) -> dict[str, Any]:
    """用关键词执行 MVP 初筛。输入 Block 与查询；输出候选 Block。"""
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
    """补齐相邻 Block。输入目标 ID；输出运行时上下文，不生成固定 Chunk。"""
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
    """定位原文。输入 Block ID；输出页码、坐标和摘录。"""
    wanted = set(block_ids)
    return {"evidence": [{
        "block_id": block.get("block_id"), "page_no": block.get("page_no"),
        "bbox": block.get("bbox"), "quote": str(block.get("text") or "")[:300],
    } for block in blocks if block.get("block_id") in wanted]}
