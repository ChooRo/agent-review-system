"""针对已解析法律文档的小型、绑定证据的元数据提取。"""

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
REFERENCE_TERMS = ("前款", "前项", "上述", "下列", "依照其规定", "另有规定")
DATE_RE = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日")
NUMBER_RE = re.compile(r"(?:中华人民共和国)?(?:国务院|主席|部)?令\s*第\s*([^\s号]{1,12})\s*号")
SKILL_NAME = "extract-legal-applicability-profile"


@lru_cache(maxsize=1)
def load_skill_instructions() -> str:
    skill_path = Path(__file__).resolve().parents[2] / "skills" / SKILL_NAME / "SKILL.md"
    if not skill_path.is_file():
        raise FileNotFoundError(f"legal applicability Skill not found: {skill_path}")
    return skill_path.read_text(encoding="utf-8")


def prepare_metadata_extraction(knowledge: dict[str, Any], document: dict[str, Any]) -> dict[str, Any]:
    """解析后立即执行低成本本地筛选；这里不调用模型。"""
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
    """选择解析引用所需最少上下文的范围单元。"""
    if not units:
        return []
    articles = sorted({int(unit["article_index"]) for unit in units if unit.get("article_index") is not None})
    edge_articles = set(articles[:3] + articles[-5:])
    matched = {
        index for index, unit in enumerate(units)
        if unit.get("article_index") in edge_articles
        or any(term in clean(unit.get("search_text") or unit.get("text")) for term in SCOPE_TERMS)
    }
    contextual = set(matched)
    for index in matched:
        text = clean(units[index].get("text"))
        if any(term in text for term in REFERENCE_TERMS) and index:
            contextual.add(index - 1)
    ranked = sorted(
        contextual,
        key=lambda index: (
            index not in matched,
            not any(term in clean(units[index].get("search_text") or units[index].get("text")) for term in SCOPE_TERMS),
            index,
        ),
    )[:24]
    # 适用范围属于文档级元数据。像“依法必须”这样的宽泛程序性词语，
    # 会把大多数义务和处罚都纳入这项小范围提取。
    return [units[index] for index in sorted(ranked)]


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
        and not re.match(r"^(第?[一二三四五六七八九十百千万0-9]+[条章节]|[（(][一二三四五六七八九十百千万0-9]+[）)])", line.strip())
    ]
    title = str(existing.get("canonical_title") or existing.get("title") or "")
    title_evidence: list[str] = []
    if title_candidates:
        # 优先使用明确的法规名称，而不是通用的命令标题和冗长前言。
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
    """仅根据已经验证的字段值生成简短介绍。"""
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
    sentences.append("具体适用以展开的法规条文证据为准")
    summary = ""
    for sentence in sentences:
        candidate = f"{summary}{sentence}。"
        if len(candidate) > 160:
            continue
        summary = candidate
    return summary or "本法规的适用信息较长，请查看展开的法规条文及其证据。"


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


def derive_task_legal_facts(
    documents: dict[str, dict[str, Any]], ledger: list[dict[str, Any]], task_context: dict[str, Any]
) -> dict[str, Any]:
    """只推导明确的采购事实；缺失信息始终保持未知。"""
    sources = []
    for role, document in documents.items():
        for block in document.get("blocks", []):
            text = str(block.get("text") or "")
            if text:
                sources.append({"source": "document", "role": role, "block_id": block.get("block_id"), "quote": text})
    for item in ledger:
        text = str(item.get("statement") or item.get("evidence_quote") or "")
        if text:
            sources.append({"source": "ledger", "item_id": item.get("item_id"), "quote": text})
    project = task_context.get("project", {}) if isinstance(task_context, dict) else {}
    for field in ("name", "project_code", "title"):
        if project.get(field):
            sources.append({"source": "project", "field": field, "quote": str(project[field])})
    if isinstance(task_context, dict) and task_context.get("title"):
        sources.append({"source": "task", "field": "title", "quote": str(task_context["title"])})

    def evidence_for(*terms: str) -> list[dict[str, Any]]:
        return [source for source in sources if any(term in source["quote"] for term in terms)][:5]

    def choice(mapping: list[tuple[str, tuple[str, ...]]]) -> tuple[str, list[dict[str, Any]]]:
        matched = [(value, evidence_for(*terms)) for value, terms in mapping if evidence_for(*terms)]
        values = {value for value, _ in matched}
        return (matched[0] if len(values) == 1 else ("unknown", [])) if matched else ("unknown", [])

    project_type, project_type_evidence = choice([
        ("engineering", ("建设工程", "工程项目", "工程采购")),
        ("goods", ("货物采购", "设备采购", "物资采购")),
        ("services", ("服务采购", "咨询服务", "技术服务")),
    ])
    procurement_method, procurement_method_evidence = choice([
        ("open_tender", ("公开招标",)), ("invited_tender", ("邀请招标",)),
        ("competitive_consultation", ("竞争性磋商",)),
        ("competitive_negotiation", ("竞争性谈判",)), ("inquiry", ("询价",)),
        ("single_source", ("单一来源",)),
    ])

    def boolean_fact(yes: tuple[str, ...], no: tuple[str, ...]) -> tuple[str, list[dict[str, Any]]]:
        no_evidence, yes_evidence = evidence_for(*no), evidence_for(*yes)
        if no_evidence and not yes_evidence:
            return "no", no_evidence
        if yes_evidence and not no_evidence:
            return "yes", yes_evidence
        return "unknown", []

    government, government_evidence = boolean_fact(("政府采购",), ("非政府采购", "不属于政府采购"))
    engineering, engineering_evidence = boolean_fact(("建设工程", "工程项目", "工程采购"), ("非工程", "不属于工程"))
    mandatory, mandatory_evidence = boolean_fact(("依法必须招标", "必须招标项目"), ("非依法必须招标", "不属于依法必须招标"))
    regions = [(match.group(0), source) for source in sources for match in re.finditer(r"[一-鿿]{2,8}(?:省|自治区|市)", source["quote"])]
    region_values = {value for value, _ in regions}
    region, region_evidence = (next(iter(region_values)), [source for value, source in regions if value == next(iter(region_values))][:3]) if len(region_values) == 1 else ("unknown", [])
    facts = {
        "project_type": project_type,
        "procurement_method": procurement_method,
        "is_government_procurement": government,
        "is_engineering_related": engineering,
        "is_mandatory_tender": mandatory,
        "region": region,
        "review_stage": "procurement_document_review",
        "evidence": {
            "project_type": project_type_evidence, "procurement_method": procurement_method_evidence,
            "is_government_procurement": government_evidence, "is_engineering_related": engineering_evidence,
            "is_mandatory_tender": mandatory_evidence, "region": region_evidence, "review_stage": [],
        },
    }
    return facts


def match_legal_documents(facts: dict[str, Any], documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """只使用明确的画像谓词；有歧义的画像保持为潜在状态。"""
    decisions = []
    for document in documents:
        profile = document.get("applicability", {})
        conditions = profile_conditions(profile)
        reasons, missing, outcomes = [], [], []
        for field, expected, profile_item in conditions:
            actual = facts.get(field, "unknown")
            outcome = "insufficient" if actual == "unknown" else "match" if actual == expected else "mismatch"
            if outcome == "insufficient":
                missing.append(field)
            outcomes.append(outcome)
            reasons.append({"field": field, "expected": expected, "actual": actual, "outcome": outcome, "profile_value": profile_item.get("value")})
        if any(value == "mismatch" for value in outcomes):
            status = "not_applicable"
        elif any(value == "insufficient" for value in outcomes):
            status = "insufficient_facts"
        elif conditions:
            status = "applicable"
        else:
            status = "potential"
            missing.append("applicability_semantics")
            reasons.append({"field": "applicability", "expected": "explicit project predicate", "actual": "not_available", "outcome": "potential"})
        source = document["source"]
        decisions.append({
            "document_key": source["document_key"], "title": source.get("title"), "status": status,
            "reasons": reasons,
            "evidence": {"task_facts": {field: facts.get("evidence", {}).get(field, []) for field, _, _ in conditions}, "profile": [item for _, _, item in conditions]},
            "missing_facts": sorted(set(missing)), "source_freeze": source["source_freeze"], "_units": document.get("units", []),
        })
    return decisions


def profile_conditions(profile: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    conditions = []
    for field in ("project_types", "activities", "trigger_conditions", "business_phases"):
        for item in profile.get(field, []) if isinstance(profile.get(field), list) else []:
            value = str(item.get("value") or "")
            if "政府采购" in value:
                conditions.append(("is_government_procurement", "yes", item))
            elif "依法必须招标" in value or "必须招标项目" in value:
                conditions.append(("is_mandatory_tender", "yes", item))
            elif "工程" in value:
                conditions.append(("is_engineering_related", "yes", item))
            elif "采购文件" in value:
                conditions.append(("review_stage", "procurement_document_review", item))
            elif "公开招标" in value:
                conditions.append(("procurement_method", "open_tender", item))
    return conditions
