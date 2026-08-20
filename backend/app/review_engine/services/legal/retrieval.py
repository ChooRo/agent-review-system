"""法律单元的单一确定性检索核心。"""

from __future__ import annotations

from typing import Any

from ..topics import canonical_topic, dictionary_topics, topic_keys


def normalize_text(value: Any) -> str:
    return "".join(str(value or "").split()).lower()


def similarity(left: str, right: str) -> float:
    """可解释的中文字符二元组相似度，作为最低优先级兜底。"""
    def grams(text: str) -> set[str]:
        value = normalize_text(text)
        return {value[index : index + 2] for index in range(max(len(value) - 1, 0))}

    left_grams, right_grams = grams(left), grams(right)
    if not left_grams or not right_grams:
        return 0.0
    return len(left_grams & right_grams) / len(left_grams | right_grams)


def retrieve_legal_units(
    *, units: list[dict[str, Any]], procurement_items: list[dict[str, Any]] | None = None,
    query: str = "", keywords: list[str] | None = None, article_no: str | None = None,
    effective_date: str | None = None, top_k: int = 10,
) -> dict[str, Any]:
    """统一执行主题、条号和相似度分层召回；调用方只负责适配输入输出。"""
    if top_k < 1:
        raise ValueError("top_k 必须为正整数")

    if procurement_items is not None:
        statements = [str(item.get("statement") or "") for item in procurement_items if item.get("statement")]
        structured_topics = {
            topic
            for item in procurement_items
            for topic in (
                topic_keys(item.get("topics"))
                or ({canonical_topic(item.get("requirement_type"))} if canonical_topic(item.get("requirement_type")) != "other" else set())
            )
        }
        synonym_topics = {
            topic for statement in statements for topic in topic_keys(dictionary_topics(statement))
        } - structured_topics
        query_topics = set()
    else:
        search_query = " ".join([query, *(keywords or [])]).strip()
        statements = [search_query] if search_query else []
        structured_topics = {canonical_topic(query)} - {"other"} if canonical_topic(query) != "other" else set()
        query_topics = topic_keys(dictionary_topics(search_query)) - structured_topics
        synonym_topics = query_topics

    ranked = []
    for unit in units:
        if article_no and unit.get("article_no") != article_no:
            continue
        if unit.get("status") == "repealed":
            continue
        if effective_date and unit.get("effective_date") and unit["effective_date"] > effective_date:
            continue
        search_text = str(unit.get("search_text") or unit.get("text") or "")
        unit_topics = topic_keys(unit.get("topics")) or topic_keys(dictionary_topics(search_text))
        exact_topics = structured_topics & unit_topics
        synonym_matches = synonym_topics & unit_topics
        article = bool(unit.get("article_no") and any(str(unit["article_no"]) in statement for statement in statements))
        fallback = max((similarity(search_text, statement) for statement in statements), default=0.0)
        if article_no:
            article = True
        order = (int(bool(exact_topics)), int(bool(synonym_matches)), int(article), fallback)
        if not any(order):
            continue
        ranked.append((order, unit, {
            "topic_exact": sorted(exact_topics),
            "topic_synonym": sorted(synonym_matches),
            "article_reference": article,
            "bigram_similarity": round(fallback, 4),
        }))

    ranked.sort(key=lambda item: item[0], reverse=True)
    selected = [
        {
            **unit,
            "retrieval_score": 4.0 if order[0] else 3.0 if order[1] else 2.0 if order[2] else round(order[3], 4),
            "retrieval_signals": signals,
        }
        for order, unit, signals in ranked[:top_k]
    ]
    warnings = ["候选法规中存在效力状态未确认的单元"] if any(unit.get("status") == "unknown" for unit in selected) else []
    return {"units": selected, "total_candidates": len(ranked), "warnings": warnings}


def rank_legal_units(
    units: list[dict[str, Any]], procurement_items: list[dict[str, Any]], top_k: int
) -> list[dict[str, Any]]:
    """Workflow 适配器：保留旧的列表返回契约。"""
    return retrieve_legal_units(units=units, procurement_items=procurement_items, top_k=top_k)["units"]
