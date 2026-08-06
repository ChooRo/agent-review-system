"""规则检索与适用性 Tool。"""

from typing import Any

from .schemas import ToolContext


def search_rules(
    *, context: ToolContext, rules: list[dict[str, Any]], scenario: str,
    text: str, top_k: int = 10
) -> dict[str, Any]:
    """筛选有效且适用于当前场景的候选规则。"""
    matched = []
    for rule in rules:
        if rule.get("status", "effective") != "effective":
            continue
        applies = rule.get("applies_to", [])
        if applies and scenario not in applies:
            continue
        keywords = [str(word) for word in rule.get("keywords", [])]
        if not keywords or any(word in text for word in keywords):
            matched.append(rule)
    return {"rules": matched[:top_k], "matched_count": min(len(matched), top_k)}


def check_rule_applicability(
    *, context: ToolContext, rule: dict[str, Any], project_context: dict[str, Any]
) -> dict[str, Any]:
    """按规则声明的项目类型和采购方式作确定性适用性初筛。"""
    constraints = rule.get("project_constraints", {})
    mismatches = [key for key, value in constraints.items() if value and project_context.get(key) not in value]
    return {"applicable": not mismatches, "reason": "满足规则约束" if not mismatches else f"不满足约束：{mismatches}", "exceptions": []}


def search_legal_units(
    *, context: ToolContext, units: list[dict[str, Any]], query: str,
    keywords: list[str] | None = None, article_no: str | None = None,
    effective_date: str | None = None, top_k: int = 10
) -> dict[str, Any]:
    """按条号和关键词检索法规原文单元；MVP使用确定性文本计分。"""
    if not 1 <= top_k <= 100:
        raise ValueError("top_k 必须在 1..100")
    terms = [term for term in [*query.split(), *(keywords or [])] if term] or [query]
    ranked = []
    for unit in units:
        if article_no and unit.get("article_no") != article_no:
            continue
        if unit.get("status") == "repealed":
            continue
        if effective_date and unit.get("effective_date") and unit["effective_date"] > effective_date:
            continue
        text = str(unit.get("search_text") or unit.get("text") or "")
        score = sum(text.count(term) for term in terms if term)
        if score or article_no:
            ranked.append((score, unit))
    ranked.sort(key=lambda pair: (pair[0], -int(pair[1].get("paragraph_no") or 0)), reverse=True)
    selected = [{**unit, "retrieval_score": score} for score, unit in ranked[:top_k]]
    warnings = ["候选法规中存在效力状态未确认的单元"] if any(unit.get("status") == "unknown" for unit in selected) else []
    return {"units": selected, "total_candidates": len(ranked), "warnings": warnings}
