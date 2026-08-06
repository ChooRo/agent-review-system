"""采购文件和合同整改复审共用 Tool。"""

from difflib import SequenceMatcher
from typing import Any

from .schemas import ToolContext


def compare_document_versions(
    *, context: ToolContext, old_items: list[dict[str, Any]], new_items: list[dict[str, Any]]
) -> dict[str, Any]:
    """按稳定事项 ID 生成新增、删除和保留清单。"""
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
    """把旧问题映射到新版最相近事项；低于阈值时明确返回未定位。"""
    ranked = sorted(
        ((SequenceMatcher(None, finding_text, str(item.get("statement") or "")).ratio(), item) for item in new_items),
        key=lambda pair: pair[0], reverse=True,
    )
    score, item = ranked[0] if ranked else (0.0, None)
    return {"matched": score >= threshold, "score": round(score, 4), "item": item if score >= threshold else None}
