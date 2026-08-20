"""审查工作流的候选事项提取、校验和问题处理工具。"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from ..legal.retrieval import similarity
from .batching import table_business_view
from .evidence import canonical_evidence_text, plain_evidence_text
from .structure import classify_structure_heading


def extraction_batch_payload(role: str, blocks: list[dict[str, Any]]) -> dict[str, Any]:
    """构造事项提取的精简输入，章节信息每批只发送一次。"""
    paths: list[list[str]] = []
    hints: list[str] = []
    for block in blocks:
        path = [str(part) for part in block.get("heading_path", []) if part]
        if path and path not in paths:
            paths.append(path)
        if block.get("block_type") == "heading" and block.get("text"):
            hint = classify_structure_heading(role, str(block["text"]))
            if hint and hint not in hints:
                hints.append(hint)
    return {
        "section_context": {"paths": paths, "category_hints": hints},
        "blocks": [_extraction_block_payload(block) for block in blocks
                   if block.get("text") and block.get("block_type") not in {"header", "footer", "page_number"}],
    }


def _extraction_block_payload(block: dict[str, Any]) -> dict[str, Any]:
    entry = {
        "id": block.get("block_id"), "t": block.get("block_type"),
        "p": block.get("page_no"), "r": block.get("role"),
    }
    table = block.get("table_business") or table_business_view({
        "block_id": block.get("block_id"), "page_no": block.get("page_no"),
        "source": {"table_html": block.get("table_html")},
    }) if block.get("block_type") == "table" and block.get("table_html") else None
    entry["tbl" if table else "x"] = table or block.get("text", "")
    if block.get("table_fragment"):
        entry["tf"] = block["table_fragment"]
    return entry


def validate_candidate_items(
    items: Any, valid_ids: set[str], blocks: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """只有完整且有来源依据的候选项才能进入正式台账。"""
    if not isinstance(items, list):
        return [], [{"reason": "items不是数组"}]
    text_by_id = {b["block_id"]: str(b.get("text") or "") for b in blocks}
    accepted, rejected = [], []
    for item in items:
        if not isinstance(item, dict):
            rejected.append({"value": item, "reason": "候选不是对象"})
            continue
        requested_ids = [str(x) for x in item.get("evidence_block_ids", [])]
        ids = [block_id for block_id in requested_ids if block_id in valid_ids]
        quote = str(item.get("evidence_quote") or "").strip()
        normalized_quote = evidence_match_text(quote)
        quote_matches = [block_id for block_id, text in text_by_id.items() if normalized_quote and normalized_quote in evidence_match_text(text)]
        location_method = "block_id"
        if not ids and quote_matches:
            ids = quote_matches[:3]
            location_method = "quote_fallback"
        if ids and normalized_quote in evidence_match_text("".join(text_by_id[block_id] for block_id in ids)):
            quote_matches = list(dict.fromkeys([*quote_matches, *ids]))
        statement = str(item.get("statement") or "").strip()
        quality_errors = candidate_quality_errors(statement, quote, ids, quote_matches)
        if quality_errors:
            rejected.append({
                "value": item,
                "reason": ",".join(quality_errors),
                "retryable": True,
            })
            continue
        category = item.get("category") or item.get("primary_category") or "未分类"
        mandatory_signal = item.get("mandatory_signal")
        accepted.append(
            {
                **item,
                "category": category,
                "value": item.get("value", item.get("source_value", "")),
                "mandatory": item.get("mandatory", mandatory_signal == "explicit_mandatory"),
                "evidence_block_ids": ids,
                "evidence_status": "verified",
                "evidence_validation": {
                    "located": bool(ids),
                    "location_method": location_method if ids else "unresolved",
                    "quote_matches_block": True,
                    "recovered_block_ids": [block_id for block_id in ids if block_id not in requested_ids],
                    "invalid_block_ids": [block_id for block_id in requested_ids if block_id not in valid_ids],
                    "reason": None,
                },
            }
        )
    return accepted, rejected


INCOMPLETE_CANDIDATE_END = re.compile(
    r"(?:并在|以及|并且|且|并|或|在|为|符合|标识|包括|如下|下列|[：:、，,])$"
)


def candidate_quality_errors(
    statement: str,
    quote: str,
    block_ids: list[str],
    quote_matches: list[str],
) -> list[str]:
    """返回确定性的准入失败；信任边界处不采用模型判断。"""
    errors: list[str] = []
    compact = re.sub(r"\s+", "", statement)
    if len(compact) < 6 or not re.search(r"[一-鿿A-Za-z0-9]", compact):
        errors.append("incomplete_statement")
    elif INCOMPLETE_CANDIDATE_END.search(compact):
        errors.append("incomplete_statement")
    if not quote:
        errors.append("evidence_quote_required")
    if not block_ids:
        errors.append("evidence_block_required")
    elif not any(block_id in quote_matches for block_id in block_ids):
        errors.append("evidence_quote_mismatch")
    return errors


def evidence_match_text(value: Any) -> str:
    """共享证据表示的向后兼容别名。"""
    return canonical_evidence_text(value)


def merge_candidate_items(model_items: Any, hard_facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """保留模型语义，然后只补充缺失的确定性事实类型。"""
    items = list(model_items) if isinstance(model_items, list) else []
    existing = {
        (str(item.get("requirement_type") or ""), tuple(item.get("evidence_block_ids", [])))
        for item in items if isinstance(item, dict)
    }
    return items + [
        item for item in hard_facts
        if (item["requirement_type"], tuple(item["evidence_block_ids"])) not in existing
    ]


def deterministic_hard_facts(role: str, blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """提取少量绝不应依赖模型召回能力的准确项目事实。"""
    if role != "procurement":
        return []
    facts: list[dict[str, Any]] = []
    methods = {
        "公开招标": "open_tender", "邀请招标": "invited_tender", "竞争性磋商": "competitive_consultation",
        "竞争性谈判": "competitive_negotiation", "询价": "inquiry", "单一来源": "single_source",
    }
    for block in blocks:
        block_id = str(block.get("block_id") or "")
        text = plain_evidence_text(block.get("text"))
        if not block_id or not text:
            continue

        project_code = re.search(r"项目编号\s*[:：]?\s*([A-Za-z0-9][A-Za-z0-9._/-]{2,})", text)
        if project_code:
            value = project_code.group(1)
            facts.append(hard_fact(block_id, "project_code", f"项目编号：{value}", "采购项目", "项目编号", value, False))

        method = next(((label, value) for label, value in methods.items() if label in text), None)
        if method:
            label, value = method
            facts.append(hard_fact(block_id, "procurement_method", f"采购方式：{label}", "采购项目", "采购方式", value, False, label))

        deadline = re.search(
            r"((?:响应|投标)截止时间\s*[:：]?\s*20\d{2}年\s*\d{1,2}月\s*\d{1,2}日(?:\s*\d{1,2}:\d{2})?(?:（北京时间）)?)",
            text,
        )
        if deadline:
            quote = re.sub(r"\s+", "", deadline.group(1))
            value = re.sub(r"^(?:响应|投标)截止时间[:：]?", "", quote)
            facts.append(hard_fact(block_id, "submission_deadline", quote, "供应商", "提交响应文件", value, True, value))
    return facts


def hard_fact(
    block_id: str, requirement_type: str, statement: str, subject: str,
    obj: str, value: str, mandatory: bool, source_value: str | None = None,
) -> dict[str, Any]:
    fingerprint = hashlib.sha1(f"{block_id}|{requirement_type}|{value}".encode()).hexdigest()[:10]
    return {
        "candidate_id": f"HARD-{fingerprint}",
        "primary_category": "项目与日程",
        "category_tags": ["项目与日程"],
        "category": "项目与日程",
        "requirement_type": requirement_type,
        "statement": statement,
        "subject": subject,
        "action": "明确" if not mandatory else "提交",
        "object": obj,
        "condition": None,
        "source_value": source_value or value,
        "normalized_value": None,
        "mandatory_signal": "explicit_mandatory" if mandatory else "explicit_fact",
        "mandatory": mandatory,
        "response_materials": [],
        "evidence_block_ids": [block_id],
        "evidence_quote": statement,
        "confidence": 1.0,
    }


def derive_candidate_hints(role: str, blocks: list[dict[str, Any]], categories: list[str]) -> list[dict[str, Any]]:
    """根据本地解析的 Block 推导候选项提示。"""
    keywords = {
        "procurement": "应|须|不得|资格|评分|报价|限价|验收|付款|合同|期限|时间",
        "response": "响应|承诺|满足|提供|偏离|报价|资质|业绩|人员|参数",
        "contract": "甲方|乙方|应|合同|金额|付款|交付|验收|质保|违约|期限",
    }[role]
    items = []
    for block in blocks:
        text = str(block.get("text") or "").strip()
        if not text or not re.search(keywords, text):
            continue
        category = classify_candidate_category(role, text, categories)
        items.append(
            {
                "category": category,
                "statement": text[:800],
                "subject": "",
                "action": "",
                "condition": "",
                "value": "",
                "mandatory": bool(re.search(r"应|须|不得|必须", text)),
                "evidence_block_ids": [block["block_id"]],
                "evidence_quote": text[:300],
            }
        )
    return items


def classify_candidate_category(role: str, text: str, categories: list[str]) -> str:
    """根据关键词对本地推导的候选项提示进行分类。"""
    maps = {
        "procurement": [
            ("资格|资质|业绩", "资格与实质性条件"),
            ("评分|分值|评审", "评审办法与评分"),
            ("技术|参数|验收", "技术需求与验收"),
            ("报价|限价|付款|结算", "商务报价与付款"),
            ("合同|违约|履约|质保", "合同履约与责任"),
            ("附件|格式|引用", "附件与引用"),
        ],
        "response": [
            ("资格|资质|证书|业绩", "资格响应"),
            ("技术|参数|验收", "技术响应"),
            ("报价|价格", "报价"),
            ("偏离", "偏离"),
            ("承诺|附件", "承诺与附件"),
        ],
        "contract": [
            ("主体|甲方|乙方", "合同主体"),
            ("金额|税率", "金额与税率"),
            ("交付|验收", "交付与验收"),
            ("付款|结算", "付款结算"),
            ("质保|服务", "质保服务"),
            ("违约", "违约责任"),
            ("保密|知识产权", "保密与知识产权"),
        ],
    }
    for pattern, category in maps[role]:
        if re.search(pattern, text):
            return category
    return categories[0]


def extraction_failure_finding(role: str, batch_no: int, failure: dict[str, Any]) -> dict[str, Any]:
    """暴露缺失的提取批次，不臆造文档证据。"""
    return {
        "finding_type": "extraction_quality",
        "risk_level": "unknown",
        "title": f"第{batch_no}批内容未完成自动提取",
        "description": "模型服务或请求预算异常，本批内容未形成完整自动审查结论。",
        "rationale": failure.get("message"),
        "recommendation": "在现有经办和主责复核环节查看该批原文后确认。",
        "document_role": role,
        "source_batch": batch_no,
        "evidence_block_ids": [],
        "evidence_quotes": [],
        "rule_ids": [],
        "legal_unit_ids": [],
        "confidence": 0.0,
        "needs_human_confirmation": True,
    }


def build_alignment_matrix(
    baseline: list[dict[str, Any]], candidates: list[dict[str, Any]], target: str
) -> list[dict[str, Any]]:
    """为每个基准事项选择最相关候选，保留证据不足而不直接作最终判定。"""
    matrix = []
    for base in baseline:
        ranked = sorted(
            ((similarity(base.get("statement", ""), item.get("statement", "")), item) for item in candidates),
            key=lambda pair: pair[0],
            reverse=True,
        )
        score, best = ranked[0] if ranked else (0.0, None)
        status = "candidate_found" if score >= 0.12 else "evidence_insufficient"
        matrix.append(
            {
                "baseline_item_id": base.get("item_id"),
                "baseline_statement": base.get("statement"),
                f"{target}_item_id": best.get("item_id") if best and status == "candidate_found" else None,
                f"{target}_statement": best.get("statement") if best and status == "candidate_found" else None,
                "retrieval_score": round(score, 4),
                "status": status,
                "baseline_evidence_block_ids": base.get("evidence_block_ids", []),
                "candidate_evidence_block_ids": best.get("evidence_block_ids", []) if best and status == "candidate_found" else [],
            }
        )
    return matrix


def deduplicate_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按问题类型和标题去重，并合并可追溯ID。"""
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in findings:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        key = (str(item.get("finding_type") or ""), str(item.get("title") or "").strip())
        if key not in unique:
            unique[key] = item
            continue
        current = unique[key]
        for field in ("evidence_block_ids", "legal_unit_ids", "rule_ids", "source_candidate_ids"):
            current[field] = list(dict.fromkeys([*current.get(field, []), *item.get(field, [])]))
    return list(unique.values())


def collect_system_warnings(quality: dict[str, Any], extraction: dict[str, Any]) -> list[dict[str, Any]]:
    """将解析、OCR和提取告警放入独立系统质量分栏。"""
    warnings = [
        {**finding, "review_scope": "system_quality"}
        for report in quality.get("quality", {}).values()
        for finding in report.get("quality_findings", [])
    ] + [
        {**finding, "review_scope": "system_quality"}
        for finding in extraction.get("extraction_findings", [])
    ]
    return deduplicate_findings(warnings)
