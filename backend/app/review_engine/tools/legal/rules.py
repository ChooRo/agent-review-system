"""规则检索与适用性 Tool。"""

from typing import Any

from ..schemas import ToolContext


def search_rules(
    *, context: ToolContext, rules: list[dict[str, Any]], scenario: str,
    text: str, top_k: int = 10
) -> dict[str, Any]:
    """作用：按当前规则资产契约筛选已发布且适用于当前模块的业务规则。
    输入：context、规则列表 rules、业务场景 scenario、待审文本 text 和 top_k。
    输出：命中的规则列表和实际返回数量。
    逻辑：依次过滤非published规则、module不匹配规则和tags不命中规则，再截取前 top_k 项。
    """
    matched = []
    for rule in rules:
        if rule.get("status") != "published":
            continue
        if rule.get("module") != scenario:
            continue
        tags = [str(tag) for tag in rule.get("tags", [])]
        if not tags or any(tag in text for tag in tags):
            matched.append(rule)
    return {"rules": matched[:top_k], "matched_count": min(len(matched), top_k)}


def check_rule_applicability(
    *, context: ToolContext, rule: dict[str, Any], project_context: dict[str, Any]
) -> dict[str, Any]:
    """作用：确定性初筛单条规则是否适用于当前项目。
    输入：context、待判断 rule 和 project_context 项目事实。
    输出：适用布尔值、判断原因及例外列表。
    逻辑：逐项核对规则声明的项目约束，任一事实不在允许值中即判为不适用。
    """
    constraints = rule.get("project_constraints", {})
    mismatches = [key for key, value in constraints.items() if value and project_context.get(key) not in value]
    return {"applicable": not mismatches, "reason": "满足规则约束" if not mismatches else f"不满足约束：{mismatches}", "exceptions": []}


def search_legal_units(
    *, context: ToolContext, units: list[dict[str, Any]], query: str,
    keywords: list[str] | None = None, article_no: str | None = None,
    effective_date: str | None = None, top_k: int = 10
) -> dict[str, Any]:
    """作用：按条号、关键词和生效时间检索法规原文单元。
    输入：context、法规 units、query、可选 keywords/article_no/effective_date 和 top_k。
    输出：排序后的法规单元、候选总数及效力状态警告。
    逻辑：过滤废止或尚未生效单元，按关键词出现次数计分排序，并提示状态未知项。
    """
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
