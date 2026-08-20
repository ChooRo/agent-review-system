"""审查问题的独立证据内容校验。"""

from __future__ import annotations

import html
import re
from typing import Any


TITLE_STOP_WORDS = {"条款", "问题", "异常", "可能", "逻辑", "解析", "缺失", "事项", "章节", "文件", "要求"}


def chinese_support_terms(title: str) -> list[str]:
    """提取简短中文术语，而不是把完整标题作为一个令牌比较。"""
    chunks = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9.%]+", title)
    terms: list[str] = []
    for chunk in chunks:
        if re.fullmatch(r"[\u4e00-\u9fff]+", chunk):
            terms.extend(chunk[index:index + 2] for index in range(len(chunk) - 1))
        else:
            terms.append(chunk)
    return [term for term in terms if term not in TITLE_STOP_WORDS]


def plain_evidence_text(value: Any) -> str:
    """将存储的 HTML 或类 Markdown 证据转换为可读的来源文本。"""
    without_tags = re.sub(r"<[^>]+>", " ", html.unescape(str(value or "")))
    return re.sub(r"\s+", " ", without_tags).strip()


def canonical_evidence_text(value: Any) -> str:
    """为模型引文和存储的 HTML 表格 Block 使用统一表示。"""
    return re.sub(r"[\W_]+", "", plain_evidence_text(value).lower(), flags=re.UNICODE)


class EvidenceValidationService:
    """校验身份、位置、引文、规则状态和结论支持信号。"""

    def validate(
        self,
        finding: dict[str, Any],
        block_index: dict[str, dict[str, Any]],
        legal_index: dict[str, dict[str, Any]],
        rule_index: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        requested = [str(value) for value in finding.get("evidence_block_ids", [])]
        valid_ids = [block_id for block_id in requested if block_id in block_index]
        invalid_ids = [block_id for block_id in requested if block_id not in block_index]
        supplied_quotes = [str(value).strip() for value in finding.get("evidence_quotes", []) if str(value).strip()]
        quote_mismatches = []
        for quote in supplied_quotes:
            if not any(canonical_evidence_text(quote) in canonical_evidence_text(block_index[block_id].get("full_text")) for block_id in valid_ids):
                quote_mismatches.append(quote)

        legal_ids = [str(value) for value in finding.get("legal_unit_ids", [])]
        invalid_legal_ids = [value for value in legal_ids if value not in legal_index]
        rule_ids = [str(value) for value in finding.get("rule_ids", [])]
        invalid_rules = [value for value in rule_ids if value not in rule_index or rule_index[value].get("status") != "published"]

        support_terms = chinese_support_terms(str(finding.get("title") or ""))
        evidence_text = "\n".join(str(block_index[block_id].get("full_text") or "") for block_id in valid_ids)
        matched_terms = [term for term in support_terms if term in evidence_text]
        support_score = 1.0 if not support_terms else len(matched_terms) / len(support_terms)
        claim_text = " ".join(str(finding.get(key) or "") for key in ("title", "description", "rationale"))
        claim_values = set(re.findall(r"\d+(?:\.\d+)?(?:%|元|万元|天|日|个月|月|年)?", claim_text))
        evidence_values = set(re.findall(r"\d+(?:\.\d+)?(?:%|元|万元|天|日|个月|月|年)?", plain_evidence_text(evidence_text)))
        values_supported = not claim_values or bool(claim_values & evidence_values)
        quoted_blocks = {
            block_id
            for block_id in valid_ids
            for quote in supplied_quotes
            if canonical_evidence_text(quote) in canonical_evidence_text(block_index[block_id].get("full_text"))
        }
        dimension_supported = (
            ("比例" in claim_text and bool(re.search(r"\d+(?:\.\d+)?\s*%|百分之", evidence_text)))
            or (any(term in claim_text for term in ("金额", "限价", "报价", "保证金")) and bool(re.search(r"\d+(?:\.\d+)?\s*(?:元|万元)", evidence_text)))
            or (any(term in claim_text for term in ("期限", "工期", "服务期", "有效期")) and bool(re.search(r"\d+(?:\.\d+)?\s*(?:天|日|月|年)", evidence_text)))
        )
        support_signal = bool(supplied_quotes and not quote_mismatches) and values_supported and (
            support_score >= 0.15 or bool(claim_values) or dimension_supported
        )
        absence_check_verified = finding.get("finding_type") == "missing_element" and bool(finding.get("absence_check_verified"))
        errors = []
        if invalid_ids:
            errors.append("invalid_block_id")
        if quote_mismatches:
            errors.append("quote_mismatch")
        if invalid_legal_ids:
            errors.append("invalid_legal_unit")
        if invalid_rules:
            errors.append("invalid_or_unpublished_rule")
        if not support_signal and not absence_check_verified:
            errors.append("conclusion_support_uncertain")
        if finding.get("finding_type") == "inconsistency" and len(valid_ids) < 2:
            errors.append("two_sided_evidence_required")
        elif finding.get("finding_type") == "inconsistency" and len(quoted_blocks) < 2:
            errors.append("two_sided_quote_support_required")
        if finding.get("finding_type") == "legal_risk" and not legal_ids:
            errors.append("legal_evidence_required")

        verified = (bool(valid_ids) or absence_check_verified) and not errors
        return {
            "valid": verified,
            "evidence_status": "verified" if verified else "evidence_insufficient",
            "valid_block_ids": valid_ids,
            "invalid_block_ids": invalid_ids,
            "quote_mismatches": quote_mismatches,
            "valid_legal_unit_ids": [value for value in legal_ids if value in legal_index],
            "invalid_legal_unit_ids": invalid_legal_ids,
            "valid_rule_ids": [value for value in rule_ids if value in rule_index and rule_index[value].get("status") == "published"],
            "invalid_rule_ids": invalid_rules,
            "errors": errors,
            "conclusion_support_score": round(support_score, 3),
            "claim_values_supported": values_supported,
            "validation_basis": "absence_check" if absence_check_verified else "source_blocks",
            "evidence": [
                {key: value for key, value in block_index[block_id].items() if key != "full_text"}
                | {"block_id": block_id, "quote": str(block_index[block_id].get("full_text") or "")}
                for block_id in valid_ids
            ],
        }
