"""审查工作流的确定性文档结构画像。"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any

from .evidence import plain_evidence_text


def merge_structure_profiles(
    base: dict[str, Any], partials: list[dict[str, Any]], quality: dict[str, Any]
) -> dict[str, Any]:
    """合并章节级结构结果，同时以确定性完整目录覆盖模型遗漏。"""
    profile = {
        "skill": "understand-document-structure",
        "skill_version": "1.0.0",
        **base,
        "quality_status": quality.get("status"),
        "section_responsibilities": list(base.get("section_responsibilities", [])),
        "parties": [],
        "terms": [],
        "references": list(base.get("references", [])),
        "clause_relations": list(base.get("clause_relations", [])),
        "global_constraints": [],
        "inventories": {
            name: list(base.get("inventories", {}).get(name, []))
            for name in ("tables", "images", "attachments")
        },
        "warnings": list(quality.get("issues", [])),
        "unresolved": [],
    }
    for key in ("section_responsibilities", "parties", "terms", "references", "clause_relations", "global_constraints", "warnings", "unresolved"):
        values = [item for partial in partials for item in partial.get(key, []) if isinstance(item, dict)]
        profile[key] = deduplicate_objects(profile.get(key, []) + values)
    for inventory in ("tables", "images", "attachments"):
        values = [
            item
            for partial in partials
            for item in partial.get("inventories", {}).get(inventory, [])
            if isinstance(item, dict)
        ]
        profile["inventories"][inventory] = deduplicate_objects(profile["inventories"][inventory] + values)
    return profile


def deduplicate_objects(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按规范化JSON去重章节批次合并结果。"""
    unique: dict[str, dict[str, Any]] = {}
    for item in items:
        unique[json.dumps(item, ensure_ascii=False, sort_keys=True)] = item
    return list(unique.values())


def block_payload(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """构造结构理解输入Block；版面噪声保留在原文层但不发送给LLM。"""
    return [
        {
            "block_id": b.get("block_id"),
            "block_type": b.get("block_type"),
            "heading_path": b.get("heading_path", []),
            "page_no": b.get("page_no"),
            "text": b.get("text", ""),
        }
        for b in blocks
        if b.get("text") and b.get("block_type") not in {"header", "footer", "page_number"}
    ]


def structure_context_for_blocks(
    profile: dict[str, Any], block_ids: set[str], include_all: bool = False
) -> dict[str, Any]:
    """将已验证的结构事实投影到批次中；摘要永远不会成为证据。"""
    def ids(item: dict[str, Any]) -> set[str]:
        values: set[str] = set()
        for key in ("block_id", "block_ids", "evidence_block_ids", "source_block_ids", "target_block_ids"):
            value = item.get(key)
            if isinstance(value, list):
                values.update(str(part) for part in value if part)
            elif value:
                values.add(str(value))
        return values

    def relevant(items: Any, limit: int = 100) -> list[dict[str, Any]]:
        if not isinstance(items, list):
            return []
        return [
            item for item in items
            if isinstance(item, dict) and (include_all or bool(ids(item) & block_ids))
        ][:limit]

    if not include_all:
        return {
            key: values
            for key, values in {
                "terms": relevant(profile.get("terms")),
                "references": relevant(profile.get("references")),
                "global_constraints": relevant(profile.get("global_constraints")),
                "unresolved": relevant(profile.get("unresolved")),
            }.items()
            if values
        }

    inventories = profile.get("inventories", {}) if isinstance(profile.get("inventories"), dict) else {}
    return {
        "quality_status": profile.get("quality_status"),
        "section_responsibilities": relevant(profile.get("section_responsibilities")),
        "terms": relevant(profile.get("terms")),
        "references": relevant(profile.get("references")),
        "global_constraints": relevant(profile.get("global_constraints")),
        "attachments": relevant(inventories.get("attachments")),
        "unresolved": relevant(profile.get("unresolved")),
        "evidence_policy": "结构画像只用于语义上下文，任何候选问题仍须引用原始Block ID。",
}


STRUCTURE_HEADING_RULES = {
    "procurement": [
        (r"公告|邀请|项目概况", "采购公告与项目概况"),
        (r"须知|前附表", "供应商须知"),
        (r"资格|资质|实质性", "资格与实质性条件"),
        (r"技术|需求|参数|验收", "技术需求与验收"),
        (r"评审|评分|评标", "评审办法与评分"),
        (r"报价|商务|付款|结算", "商务报价与付款"),
        (r"合同|履约|违约", "合同范本与履约"),
        (r"附件|格式|响应文件", "附件与格式文件"),
        (r"目录", "目录"),
    ],
    "response": [
        (r"资格|资质|证明", "资格响应"),
        (r"技术|参数|验收", "技术响应"),
        (r"商务|报价|价格", "商务与报价响应"),
        (r"偏离|承诺", "偏离与承诺"),
        (r"附件|目录", "附件与目录"),
    ],
    "contract": [
        (r"主体|甲方|乙方|当事人", "合同主体"),
        (r"标的|范围|内容", "合同标的与范围"),
        (r"金额|价款|税率", "金额与税率"),
        (r"交付|履约|验收|质保", "履约与验收"),
        (r"付款|结算", "付款结算"),
        (r"违约|责任|争议", "责任与争议"),
        (r"保密|知识产权", "保密与知识产权"),
        (r"附件|目录", "附件与目录"),
    ],
}


def classify_structure_heading(role: str, title: str) -> str | None:
    """按文档角色把明确标题映射为章节职责；无法判断时返回None。"""
    return next((label for pattern, label in STRUCTURE_HEADING_RULES[role] if re.search(pattern, title)), None)


def structure_review_batches(
    blocks: list[dict[str, Any]], role: str, max_chars: int
) -> list[list[dict[str, Any]]]:
    """仅选择无标题文档或整批没有可分类标题的章节批次交给LLM。"""
    batches = section_batches(blocks, max_chars)
    headings = [b for b in blocks if b.get("block_type") == "heading" and b.get("text")]
    if not headings:
        return batches
    return [
        batch
        for batch in batches
        if not any(
            b.get("block_type") == "heading"
            and b.get("text")
            and classify_structure_heading(role, str(b["text"]))
            for b in batch
        )
    ]


def deterministic_inventories(blocks: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """从Block类型和附件标题生成无需LLM的表格、图片与附件清单。"""
    def item(block: dict[str, Any]) -> dict[str, Any]:
        return {
            "block_id": block.get("block_id"),
            "page_no": block.get("page_no"),
            "title": str(block.get("text") or "")[:200],
        }

    return {
        "tables": [item(b) for b in blocks if b.get("block_type") == "table"],
        "images": [item(b) for b in blocks if b.get("block_type") == "image"],
        "attachments": [
            item(b)
            for b in blocks
            if b.get("block_type") == "heading" and re.search(r"附件|附录|格式", str(b.get("text") or ""))
        ],
    }


def deterministic_clause_relations(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """识别条款、段落和项目的从属关系，同时保留原始 Block ID。"""
    relations, current_article, current_item = [], None, None
    for block in blocks:
        text = str(block.get("text") or "").strip()
        if re.match(r"^第[一二三四五六七八九十百千万\d]+条", text):
            current_article, current_item = block.get("block_id"), None
            continue
        if re.match(r"^[（(][一二三四五六七八九十\d]+[）)]", text):
            if current_article:
                relations.append({"parent_block_id": current_article, "child_block_id": block.get("block_id"), "relation_type": "article_item"})
            current_item = block.get("block_id")
            continue
        if re.match(r"^\d+[.、]", text) and (current_item or current_article):
            relations.append({"parent_block_id": current_item or current_article, "child_block_id": block.get("block_id"), "relation_type": "item_subitem"})
    return relations


def deterministic_references(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """全局解析明确的附件、章节、前置表格和编号条款引用。"""
    targets: dict[str, list[str]] = defaultdict(list)
    reference_pattern = re.compile(
        r"附件\s*[一二三四五六七八九十\d]+|"
        r"第[一二三四五六七八九十\d]+章(?:第?\s*\d+(?:\.\d+)*\s*款)?|"
        r"前附表|第\s*\d+(?:\.\d+)*\s*款"
    )
    def keys(text: str) -> set[str]:
        return {re.sub(r"\s+", "", value) for value in reference_pattern.findall(text)}

    for block in blocks:
        text = plain_evidence_text(block.get("text"))
        if block.get("block_type") == "heading" or "前附表" in text or re.match(r"^\s*\d+(?:\.\d+)+", text):
            for key in keys(text):
                targets[key].append(block.get("block_id"))
            number = re.match(r"^\s*(\d+(?:\.\d+)+)", text)
            if number:
                targets[f"第{number.group(1)}款"].append(block.get("block_id"))
    references = []
    for block in blocks:
        text = plain_evidence_text(block.get("text"))
        if not re.search(r"详见|参见|见第|见前附表|依据|按照", text):
            continue
        for value in reference_pattern.findall(text):
            key = re.sub(r"\s+", "", value)
            if block.get("block_id") in targets.get(key, []):
                continue
            matched = list(dict.fromkeys(targets.get(key, [])))
            if not matched and "章" in key:
                chapter, clause = key.split("章", 1)
                matched = list(dict.fromkeys(targets.get(clause, []) or targets.get(chapter + "章", [])))
            references.append({"reference_text": value, "source_block_ids": [block.get("block_id")], "target_block_ids": matched, "relation_type": "attachment_reference", "status": "resolved" if len(matched) == 1 else "ambiguous" if matched else "unresolved", "confidence": 1.0 if len(matched) == 1 else 0.5})
    return references


def section_batches(blocks: list[dict[str, Any]], max_chars: int) -> list[list[dict[str, Any]]]:
    """按标题优先、字符上限兜底生成运行时章节批次，不持久化固定Chunk。"""
    batches: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    size = 0
    for block in blocks:
        text = str(block.get("text") or "")
        text_size = len(text)
        if text_size > max_chars:
            if current:
                batches.append(current)
                current, size = [], 0
            for fragment_index, start in enumerate(range(0, text_size, max_chars), start=1):
                fragment = {
                    **block,
                    "text": text[start : start + max_chars],
                    "runtime_fragment": {
                        "index": fragment_index,
                        "char_range": [start, min(start + max_chars, text_size)],
                    },
                }
                batches.append([fragment])
            continue
        starts_section = block.get("block_type") == "heading" and current
        if current and (size + text_size > max_chars or starts_section and size > max_chars // 3):
            batches.append(current)
            current, size = [], 0
        current.append(block)
        size += text_size
    if current:
        batches.append(current)
    return batches or [[]]


def batch_manifest(batch_no: int, blocks: list[dict[str, Any]]) -> dict[str, Any]:
    """持久化运行批次的可读记录，不重复保存完整文本。"""
    headings = [str(block.get("text") or "") for block in blocks if block.get("block_type") == "heading"]
    return {
        "batch_no": batch_no,
        "block_count": len(blocks),
        "character_count": sum(len(str(block.get("text") or "")) for block in blocks),
        "page_range": [min((block.get("page_no") or 0 for block in blocks), default=0), max((block.get("page_no") or 0 for block in blocks), default=0)],
        "heading": headings[0] if headings else "无独立标题的内容单元",
        "block_ids": [block.get("block_id") for block in blocks],
    }
