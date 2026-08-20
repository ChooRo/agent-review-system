"""无损采购断言台账和业务事项聚类。"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from difflib import SequenceMatcher
from typing import Any

from ..topics import assertion_topics


def normalize_text(value: Any) -> str:
    return re.sub(r"[\s，。；：、,.;:（）()]+", "", str(value or "")).lower()


def assertion_signature(item: dict[str, Any]) -> str:
    fields = ("original_subject", "subject", "action", "object", "condition", "source_value", "statement")
    return "|".join(normalize_text(item.get(field)) for field in fields)


REFERENCE_VALUE = re.compile(r"(?:详见|参见|见第|见前附表|以.+为准)")
DIMENSION_TERMS = {
    "amount": ("金额", "限价", "报价", "费用", "价款", "保证金", "人民币", "元"),
    "duration": ("期限", "工期", "服务期", "有效期", "质保期", "日内", "天", "个月", "年"),
    "location": ("地点", "地址", "场所", "武汉", "指定地点"),
    "percentage": ("比例", "税率", "费率", "%", "百分之"),
    "date": ("日期", "时间", "截止", "开标", "提交"),
    "quantity": ("数量", "不少于", "不超过", "项", "份", "人", "辆"),
}


def value_dimension(item: dict[str, Any]) -> str:
    """推断保守的比较维度，不覆盖来源值。"""
    normalized = item.get("normalized_value")
    unit = normalize_text(normalized.get("unit")) if isinstance(normalized, dict) else ""
    if unit in {"cny", "rmb", "元", "万元"}:
        return "amount"
    if unit in {"day", "days", "month", "months", "year", "years", "天", "日", "月", "年"}:
        return "duration"
    if unit in {"percent", "%"}:
        return "percentage"
    text = " ".join(str(item.get(field) or "") for field in ("statement", "action", "object", "source_value"))
    for dimension, terms in DIMENSION_TERMS.items():
        if any(term in text for term in terms):
            return dimension
    return "unknown"


class LedgerService:
    """在聚类前保留提取出现记录和来源断言。"""

    def build(self, role: str, candidates: list[dict[str, Any]], document_version_id: str) -> dict[str, Any]:
        occurrences: list[dict[str, Any]] = []
        assertions: list[dict[str, Any]] = []
        exact: dict[tuple[Any, ...], str] = {}
        by_id: dict[str, dict[str, Any]] = {}

        for index, candidate in enumerate(candidates, start=1):
            occurrence_id = f"EXT-{hashlib.sha1(f'{document_version_id}|{candidate.get('source_batch')}|{index}'.encode()).hexdigest()[:12]}"
            occurrences.append({"occurrence_id": occurrence_id, "document_version_id": document_version_id, "batch_no": candidate.get("source_batch"), "model_call": candidate.get("model_call"), "candidate_id": candidate.get("candidate_id"), "accepted": True})
            evidence = tuple(sorted(str(value) for value in candidate.get("evidence_block_ids", [])))
            key = (document_version_id, evidence, assertion_signature(candidate), normalize_text(candidate.get("source_value")), normalize_text(candidate.get("condition")))
            if key in exact:
                by_id[exact[key]]["extraction_occurrence_ids"].append(occurrence_id)
                continue
            assertion_id = "AST-" + hashlib.sha1(repr(key).encode()).hexdigest()[:12]
            raw_requirement_type = candidate.get("requirement_type")
            requirement_type, topics = assertion_topics(
                raw_requirement_type,
                " ".join(str(candidate.get(field) or "") for field in ("statement", "action", "object", "source_value")),
            )
            attributes = dict(candidate.get("attributes") or {})
            if raw_requirement_type and requirement_type == "other":
                attributes.setdefault("original_requirement_type", str(raw_requirement_type))
            assertion = {
                "assertion_id": assertion_id,
                "document_version_id": document_version_id,
                "category": candidate.get("category") or candidate.get("primary_category") or "未分类",
                "category_tags": list(candidate.get("category_tags") or []),
                "requirement_type": requirement_type,
                "topics": topics,
                "attributes": attributes,
                "statement": candidate.get("statement") or candidate.get("evidence_quote") or "",
                "original_subject": candidate.get("original_subject", candidate.get("subject", "")),
                "canonical_subject": candidate.get("canonical_subject", candidate.get("subject", "")),
                "action": candidate.get("action", ""),
                "object": candidate.get("object", ""),
                "condition": candidate.get("condition", ""),
                "source_value": candidate.get("source_value", candidate.get("value", "")),
                "normalized_value": candidate.get("normalized_value"),
                "value_dimension": candidate.get("value_dimension") or value_dimension(candidate),
                "mandatory_signal": candidate.get("mandatory_signal", "explicit_mandatory" if candidate.get("mandatory") else "not_explicit"),
                "evidence_block_ids": list(evidence),
                "evidence_quote": candidate.get("evidence_quote", ""),
                "evidence_status": candidate.get("evidence_status", "evidence_insufficient"),
                "evidence_validation": candidate.get("evidence_validation", {}),
                "confidence": candidate.get("confidence"),
                "extraction_occurrence_ids": [occurrence_id],
                "relations": [],
            }
            exact[key] = assertion_id
            by_id[assertion_id] = assertion
            assertions.append(assertion)

        clusters = self._cluster(assertions)
        return {"schema_version": 1, "document_role": role, "document_version_id": document_version_id, "extraction_occurrences": occurrences, "source_assertions": assertions, "business_item_clusters": clusters}

    def _cluster(self, assertions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for assertion in assertions:
            subject = normalize_text(assertion.get("canonical_subject"))
            action = normalize_text(assertion.get("action"))
            obj = normalize_text(assertion.get("object"))
            dimension = assertion.get("value_dimension") or "unknown"
            key = f"{assertion['category']}|{subject}|{action}|{obj}|{dimension}"
            if not subject and not action and not obj:
                key = f"{assertion['category']}|{normalize_text(assertion['statement'])[:24]}"
            groups[key].append(assertion)

        clusters = []
        for key, items in groups.items():
            cluster_id = "REQ-" + hashlib.sha1(key.encode()).hexdigest()[:10]
            relations = []
            for left_index, left in enumerate(items):
                for right in items[left_index + 1:]:
                    relation = self._relation(left, right)
                    relations.append({"left_assertion_id": left["assertion_id"], "right_assertion_id": right["assertion_id"], "relation_type": relation})
                    left["relations"].append({"target_assertion_id": right["assertion_id"], "relation_type": relation})
                    right["relations"].append({"target_assertion_id": left["assertion_id"], "relation_type": relation})
            clusters.append({"item_id": cluster_id, "category": items[0]["category"], "canonical_subject": next((item["canonical_subject"] for item in items if item.get("canonical_subject")), ""), "value_dimension": items[0].get("value_dimension", "unknown"), "assertion_ids": [item["assertion_id"] for item in items], "relations": relations, "comparison_values": [{"assertion_id": item["assertion_id"], "source_value": item.get("source_value"), "normalized_value": item.get("normalized_value"), "value_dimension": item.get("value_dimension"), "condition": item.get("condition")} for item in items]})
        return clusters

    @staticmethod
    def _relation(left: dict[str, Any], right: dict[str, Any]) -> str:
        if left["evidence_block_ids"] == right["evidence_block_ids"] and assertion_signature(left) == assertion_signature(right):
            return "duplicate"
        left_reference_text = f"{left.get('statement') or ''} {left.get('source_value') or ''}"
        right_reference_text = f"{right.get('statement') or ''} {right.get('source_value') or ''}"
        if REFERENCE_VALUE.search(left_reference_text) or REFERENCE_VALUE.search(right_reference_text):
            return "cross_reference"
        left_dimension = left.get("value_dimension") or value_dimension(left)
        right_dimension = right.get("value_dimension") or value_dimension(right)
        if left_dimension != right_dimension:
            return "supplementary"
        left_value, right_value = normalize_text(left.get("source_value")), normalize_text(right.get("source_value"))
        if left_dimension != "unknown" and left_value and right_value and left_value != right_value:
            return "conflicting"
        score = SequenceMatcher(None, normalize_text(left["statement"]), normalize_text(right["statement"])).ratio()
        if score >= 0.85:
            return "equivalent"
        if score >= 0.45:
            return "supplementary"
        return "uncertain"


class SceneViewService:
    """构建分类视图，不破坏断言级来源信息。"""

    def build(self, ledger: dict[str, Any]) -> dict[str, Any]:
        assertions = {item["assertion_id"]: item for item in ledger.get("source_assertions", [])}
        topic_views: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for cluster in ledger.get("business_item_clusters", []):
            topic_views[cluster["category"]].append({**cluster, "assertions": [assertions[item_id] for item_id in cluster["assertion_ids"] if item_id in assertions]})
        return {"scenario": "procurement", "topic_views": dict(topic_views)}


def procurement_assertions(value: Any) -> list[dict[str, Any]]:
    """读取当前三层台账和旧版扁平检查点产物。"""
    if isinstance(value, list):
        return value
    return value.get("source_assertions", []) if isinstance(value, dict) else []
