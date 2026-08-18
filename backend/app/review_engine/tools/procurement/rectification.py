"""采购文件和合同整改复审共用 Tool。"""

from difflib import SequenceMatcher
from typing import Any

from ..schemas import ToolContext


def compare_document_versions(
    *, context: ToolContext, old_items: list[dict[str, Any]], new_items: list[dict[str, Any]]
) -> dict[str, Any]:
    """作用：比较整改前后文档事项的增删与保留情况。
    输入：context、旧版事项 old_items 和新版事项 new_items。
    输出：added、removed、unchanged_or_updated 三类清单。
    逻辑：分别按稳定 item_id 建索引，再通过键集合的差集和交集分类。
    """
    old = {item.get("item_id"): item for item in old_items}
    new = {item.get("item_id"): item for item in new_items}
    return {
        "added": [new[key] for key in new.keys() - old.keys()],
        "removed": [old[key] for key in old.keys() - new.keys()],
        "unchanged_or_updated": [{"old": old[key], "new": new[key]} for key in old.keys() & new.keys()],
    }


def map_finding_to_new_version(
    *, context: ToolContext, finding_text: str, new_items: list[dict[str, Any]], threshold: float = 0.25
) -> dict[str, Any]:
    """作用：把旧版审查问题映射到新版最相近事项。
    输入：context、问题文本 finding_text、新版事项 new_items 和相似度 threshold。
    输出：是否命中、最高分及命中的新版事项。
    逻辑：计算问题与各事项陈述的文本相似度，取最高项并用阈值决定是否返回。
    """
    ranked = sorted(
        ((SequenceMatcher(None, finding_text, str(item.get("statement") or "")).ratio(), item) for item in new_items),
        key=lambda pair: pair[0], reverse=True,
    )
    score, item = ranked[0] if ranked else (0.0, None)
    return {"matched": score >= threshold, "score": round(score, 4), "item": item if score >= threshold else None}
