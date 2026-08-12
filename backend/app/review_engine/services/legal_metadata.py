"""Small, evidence-bound metadata extraction for parsed legal documents."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any


APPLICABILITY_FIELDS = (
    "activities", "subjects", "business_phases", "trigger_conditions", "project_types",
    "exclusions", "precedence_rules",
)
SCOPE_TERMS = (
    "适用", "不适用", "除外", "另有规定", "依照其规定", "本法所称", "本条例所称",
    "范围", "境内", "政府采购", "优先适用", "参照执行",
)
DATE_RE = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日")
NUMBER_RE = re.compile(r"(?:中华人民共和国)?(?:国务院|主席|部)?令\s*第\s*([^\s号]{1,12})\s*号")
SKILL_NAME = "extract-legal-applicability-profile"


@lru_cache(maxsize=1)
def load_skill_instructions() -> str:
    skill_path = Path(__file__).resolve().parents[1] / "skills" / SKILL_NAME / "SKILL.md"
    if not skill_path.is_file():
        raise FileNotFoundError(f"legal applicability Skill not found: {skill_path}")
    return skill_path.read_text(encoding="utf-8")


def prepare_metadata_extraction(knowledge: dict[str, Any], document: dict[str, Any]) -> dict[str, Any]:
    """Run cheap local selection immediately after parsing; no model call here."""
    units = knowledge.get("units", [])
    candidates = select_candidate_units(units)
    basic, evidence = infer_basic_information(document, knowledge.get("legal_document", {}))
    return {
        "status": "pending_ai",
        "candidate_unit_ids": [unit["legal_unit_id"] for unit in candidates],
        "candidate_count": len(candidates),
        "total_unit_count": len(units),
        "basic_information": basic,
        "field_evidence": evidence,
        "warnings": [],
        "updated_at": datetime.now(UTC).isoformat(),
    }


def select_candidate_units(units: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Select scope/exception units plus the beginning and end; never send the whole law by default."""
    if not units:
        return []
    articles = sorted({int(unit["article_index"]) for unit in units if unit.get("article_index") is not None})
    edge_articles = set(articles[:3] + articles[-5:])
    selected = [
        unit for unit in units
        if unit.get("article_index") in edge_articles
        or any(term in str(unit.get("search_text") or unit.get("text") or "") for term in SCOPE_TERMS)
    ]
    # Applicability is document-level metadata. Broad procedural terms such as
    # "依法必须" would pull most duties and penalties into this small extraction.
    return selected[:24]


def candidate_batches(units: list[dict[str, Any]], max_units: int = 24, max_chars: int = 8000) -> list[list[dict[str, Any]]]:
    batches: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    chars = 0
    for unit in units:
        size = len(str(unit.get("search_text") or unit.get("text") or ""))
        if current and (len(current) >= max_units or chars + size > max_chars):
            batches.append(current)
            current, chars = [], 0
        current.append(unit)
        chars += size
    if current:
        batches.append(current)
    return batches


def infer_basic_information(document: dict[str, Any], existing: dict[str, Any]) -> tuple[dict[str, Any], dict[str, list[str]]]:
    blocks = document.get("blocks", [])
    head_tail = blocks[:30] + blocks[-15:]
    texts = [(str(block.get("block_id") or ""), clean(block.get("text"))) for block in head_tail]
    title_candidates = [
        (block_id, line.strip("《》"))
        for block_id, text in texts for line in text.splitlines()
        if 4 <= len(line.strip("《》 ")) <= 80
        and any(word in line for word in ("法", "条例", "规定", "办法"))
        and not line.startswith(("关于", "根据"))
    ]
    title = str(existing.get("canonical_title") or existing.get("title") or "")
    title_evidence: list[str] = []
    if title_candidates:
        # Prefer a named instrument over generic order headings and long preambles.
        block_id, inferred = min(title_candidates, key=lambda item: ("令" in item[1], len(item[1])))
        title, title_evidence = inferred, [block_id]
    joined = "\n".join(text for _, text in texts)
    number_match = NUMBER_RE.search(joined)
    document_number = existing.get("document_number")
    if number_match:
        prefix = "国务院令" if "国务院令" in number_match.group(0) else "令"
        document_number = f"{prefix}第{number_match.group(1)}号"
    legal_level = existing.get("legal_level") or (
        "law" if title.endswith("法") else "administrative_regulation" if "条例" in title else "other"
    )
    issuer = existing.get("issuer")
    if not issuer:
        if "国务院令" in joined:
            issuer = "国务院"
        elif "全国人民代表大会常务委员会" in joined:
            issuer = "全国人民代表大会常务委员会"
    dates = DATE_RE.findall(joined)
    normalized_dates = [f"{year}-{int(month):02d}-{int(day):02d}" for year, month, day in dates]
    original_effective = existing.get("original_effective_date")
    if not original_effective:
        for _, text in reversed(texts):
            if "施行" in text and (match := DATE_RE.search(text)):
                original_effective = f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
                break
    basic = {
        "canonical_title": title,
        "legal_level": legal_level,
        "document_number": document_number,
        "issuer": issuer,
        "adoption_date": existing.get("adoption_date"),
        "promulgation_date": existing.get("promulgation_date") or (normalized_dates[0] if normalized_dates else None),
        "original_effective_date": original_effective,
        "revision_date": existing.get("revision_date"),
        "current_version_effective_date": existing.get("current_version_effective_date") or existing.get("effective_date"),
    }
    evidence = {"canonical_title": title_evidence}
    return basic, evidence


def extract_applicability(llm: Any, units: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    merged = {field: [] for field in APPLICABILITY_FIELDS}
    warnings: list[dict[str, Any]] = []
    valid = {unit["legal_unit_id"]: unit for unit in units}
    prompt = load_skill_instructions()
    for batch_no, batch in enumerate(candidate_batches(units), 1):
        payload = {"units": [unit_payload(unit) for unit in batch]}
        result = llm.json_call("legal_metadata", prompt, payload)
        applicability = result.get("applicability", {})
        for field in APPLICABILITY_FIELDS:
            for item in applicability.get(field, []) if isinstance(applicability, dict) else []:
                accepted, reason = validate_item(item, valid)
                if accepted:
                    merged[field].append(accepted)
                else:
                    warnings.append({"code": "INVALID_AI_EVIDENCE", "field": field, "batch": batch_no, "reason": reason})
    applicability = {field: deduplicate(items) for field, items in merged.items()}
    applicability["summary"] = summarize_applicability(applicability)
    return applicability, warnings


def summarize_applicability(applicability: dict[str, list[dict[str, Any]]]) -> str:
    """Compose a short introduction solely from already verified field values."""
    def values(field: str, limit: int = 2) -> str:
        return "、".join(clean(item.get("value")) for item in applicability.get(field, [])[:limit] if clean(item.get("value")))

    activities = values("activities") or values("project_types")
    subjects = values("subjects")
    phases = values("business_phases")
    triggers = values("trigger_conditions")
    boundaries = values("exclusions") or values("precedence_rules")
    sentences = []
    if activities:
        sentences.append(f"本法规适用于{activities}等活动")
    if subjects:
        sentences.append(f"主要面向{subjects}")
    if phases:
        sentences.append(f"覆盖{phases}等业务阶段")
    if triggers:
        sentences.append(f"适用时需关注{triggers}")
    if boundaries:
        sentences.append(f"关键边界包括{boundaries}")
    if not sentences:
        return "本法规的适用信息尚未从可核验条款中提取完成，请查看展开的法规条文及其证据。"
    summary = "。".join(sentences) + "。具体适用以展开的法规条文证据为准。"
    return summary[:160]


def validate_item(item: Any, units: dict[str, dict[str, Any]]) -> tuple[dict[str, Any] | None, str]:
    if not isinstance(item, dict) or not clean(item.get("value")):
        return None, "missing value"
    value = clean(item["value"])
    accepted_evidence = []
    for evidence in item.get("evidence", []) if isinstance(item.get("evidence"), list) else []:
        unit_id = str(evidence.get("legal_unit_id") or "") if isinstance(evidence, dict) else ""
        quote = clean(evidence.get("quote")) if isinstance(evidence, dict) else ""
        unit = units.get(unit_id)
        source = clean(unit.get("search_text") or unit.get("text")) if unit else ""
        if unit and quote and quote in source and value in source:
            accepted_evidence.append({"legal_unit_id": unit_id, "quote": quote})
    if not accepted_evidence:
        return None, "value is not supported by a legal_unit_id/quote pair"
    return {"value": value, "evidence": accepted_evidence}, ""


def deduplicate(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for item in items:
        key = re.sub(r"[\s，。；、,.;]", "", item["value"]).lower()
        if key not in merged:
            merged[key] = item
            continue
        seen = {(value["legal_unit_id"], value["quote"]) for value in merged[key]["evidence"]}
        merged[key]["evidence"].extend(value for value in item["evidence"] if (value["legal_unit_id"], value["quote"]) not in seen)
    return list(merged.values())


def unit_payload(unit: dict[str, Any]) -> dict[str, Any]:
    return {
        "legal_unit_id": unit.get("legal_unit_id"),
        "article_no": unit.get("article_no"),
        "chapter": unit.get("chapter"),
        "text": unit.get("text"),
        "parent_context": unit.get("parent_context"),
    }


def clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()
