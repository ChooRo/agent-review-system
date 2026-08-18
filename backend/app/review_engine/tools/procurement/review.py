"""采购文件审查专用 Tool。"""

from typing import Any

from ..schemas import ToolContext


def get_procurement_topic_view(*, context: ToolContext, topic_views: dict[str, Any]) -> dict[str, Any]:
    """作用：读取采购事项的主题视图。
    输入：context 和 topic_views 场景视图。
    输出：主题视图的浅拷贝。
    逻辑：复制输入字典，避免工具调用修改工作流中的原始视图。
    """
    return dict(topic_views)


def check_required_elements(
    *, context: ToolContext, topic_views: dict[str, Any], required: list[str]
) -> dict[str, Any]:
    """作用：检查采购文件是否覆盖所有必备主题。
    输入：context、已有 topic_views 和必备主题 required。
    输出：缺失主题、完整性状态及是否需要人工确认。
    逻辑：对必备主题与现有主题键做集合差集，并按缺失情况生成状态。
    """
    missing = sorted(set(required) - set(topic_views))
    return {"missing": missing, "complete": not missing, "needs_human_confirmation": bool(missing)}


def evaluate_rule_coverage(
    *, context: ToolContext, rules: list[dict[str, Any]], assertions: list[dict[str, Any]],
    library_status: str = "loaded",
) -> dict[str, Any]:
    """作用：评估已发布规则在采购原文断言中的关键词覆盖情况。
    输入：context、规则 rules 和原文断言 assertions。
    输出：逐规则覆盖结果和满足规则数量。
    逻辑：拼接断言文本，仅检查已发布规则；无标签视为满足，否则按标签命中判断。
    """
    text = "\n".join(str(item.get("statement") or "") for item in assertions)
    results = []
    for rule in rules:
        if rule.get("status") != "published":
            continue
        tags = [str(value) for value in rule.get("tags", []) if str(value)]
        covered = not tags or any(tag in text for tag in tags)
        results.append({"rule_id": rule.get("id"), "status": "satisfied" if covered else "evidence_insufficient", "matched_tags": [tag for tag in tags if tag in text]})
    covered = sum(item["status"] == "satisfied" for item in results)
    return {
        "library_status": library_status,
        "executable_rule_count": len(results),
        "matched_count": covered,
        "results": results,
        "covered_count": covered,
    }


def compare_procurement_sections(
    *, context: ToolContext, assertions: list[dict[str, Any]]
) -> dict[str, Any]:
    """作用：汇总采购文件跨章节冲突关系。
    输入：context 和带 relations 的 assertions。
    输出：冲突明细及冲突数量。
    逻辑：遍历每条断言的关系，只保留 relation_type 为 conflicting 的项目。
    """
    conflicts = [{"assertion_id": assertion.get("assertion_id"), **relation} for assertion in assertions for relation in assertion.get("relations", []) if relation.get("relation_type") == "conflicting"]
    return {"conflicts": conflicts, "count": len(conflicts)}
