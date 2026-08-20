"""法规文件解析、条款单元构建和质量检查。"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from ..mineru import MinerUService, namespace_block_ids
from ..runtime import write_json
from ..topics import dictionary_topics


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
CHAPTER_RE = re.compile(r"^第\s*([〇零一二三四五六七八九十百千两\d]+)\s*章\s*(.*)$")
SECTION_RE = re.compile(r"^第\s*([〇零一二三四五六七八九十百千两\d]+)\s*节\s*(.*)$")
ARTICLE_RE = re.compile(r"^第\s*([〇零一二三四五六七八九十百千两\d]+)\s*条[　\s]*(.*)$")
ITEM_RE = re.compile(r"^[（(]([一二三四五六七八九十百\d]+)[）)]\s*(.*)$")
REFERENCE_RE = re.compile(r"第\s*[〇零一二三四五六七八九十百千两\d]+\s*条")


def ingest_legal_document(
    source: Path,
    output_dir: Path,
    mineru: MinerUService,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """解析法规并写出原始Document JSON和法规知识JSON。"""
    source = source.resolve()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        document = mineru.parse(source, output_dir / "parser", "legal")
    except Exception as exc:
        if source.suffix.lower() != ".docx":
            raise
        document = parse_docx(source)
        document["parser"].update({
            "fallback_from": "mineru",
            "fallback_reason": f"{type(exc).__name__}: {exc}",
        })
    knowledge = build_legal_knowledge(document, metadata or {})
    write_json(output_dir / "document.json", document)
    write_json(output_dir / "legal_knowledge.json", knowledge)
    return knowledge


def parse_docx(source: Path) -> dict[str, Any]:
    """直接读取DOCX段落与表格，输出和MinerU兼容的Block结构。"""
    namespace = {"w": W_NS}
    with ZipFile(source) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
    body = root.find("w:body", namespace)
    if body is None:
        raise ValueError("DOCX中没有正文")
    blocks: list[dict[str, Any]] = []
    for element in body:
        kind = element.tag.rsplit("}", 1)[-1]
        if kind == "p":
            text = "".join(node.text or "" for node in element.findall(".//w:t", namespace)).strip()
            block_type = "paragraph"
        elif kind == "tbl":
            rows = []
            for row in element.findall(".//w:tr", namespace):
                cells = ["".join(node.text or "" for node in cell.findall(".//w:t", namespace)).strip() for cell in row.findall("w:tc", namespace)]
                rows.append(" | ".join(cells))
            text = "\n".join(row for row in rows if row.strip(" |"))
            block_type = "table"
        else:
            continue
        if not text:
            continue
        index = len(blocks) + 1
        blocks.append({
            "block_id": f"B-{index:05d}",
            "block_type": block_type,
            "heading_path": [],
            "text": text,
            "page_no": None,
            "bbox": None,
            "reading_order": index,
            "source": {"docx_element": kind},
        })
    document = {
        "document_id": source.stem,
        "document_role": "legal",
        "source_file": str(source),
        "parser": {"name": "docx-direct", "source": "word/document.xml"},
        "blocks": blocks,
    }
    namespace_block_ids(document, "legal")
    return document


def build_legal_knowledge(document: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
    """把顺序Block重组为带父级上下文和证据映射的条、款、项单元。"""
    # 上传表单标题只是标签，不是权威的法律元数据。
    # 始终根据解析后的法律文本生成展示标题和文档标题。
    title = infer_title(document)
    document_key = hashlib.sha1(title.encode("utf-8")).hexdigest()[:10]
    units: list[dict[str, Any]] = []
    chapter = section = ""
    article_no = ""
    article_index: int | None = None
    paragraph_no = 0
    parent_paragraph = ""
    article_seen = False

    for block in document.get("blocks", []):
      for raw_text in str(block.get("text") or "").splitlines() or [""]:
        text = normalize_legal_text(raw_text)
        if not text:
            continue
        chapter_match = CHAPTER_RE.match(text)
        section_match = SECTION_RE.match(text)
        article_match = ARTICLE_RE.match(text)
        if chapter_match:
            chapter = f"第{chapter_match.group(1)}章" + (f" {chapter_match.group(2).strip()}" if chapter_match.group(2).strip() else "")
            section = ""
            continue
        if section_match:
            section = f"第{section_match.group(1)}节" + (f" {section_match.group(2).strip()}" if section_match.group(2).strip() else "")
            continue
        if article_match:
            article_seen = True
            raw_number, text = article_match.groups()
            article_no = f"第{raw_number}条"
            article_index = chinese_number(raw_number)
            paragraph_no = 0
            parent_paragraph = ""
            text = text.strip()
            if not text:
                continue
        elif not article_seen:
            continue

        item_match = ITEM_RE.match(text)
        if item_match:
            item_no, item_text = item_match.groups()
            if paragraph_no == 0:
                paragraph_no = 1
            units.append(make_unit(
                document_key, title, metadata, chapter, section, article_no, article_index,
                paragraph_no, item_no, item_text, parent_paragraph, block,
            ))
        else:
            paragraph_no += 1
            parent_paragraph = text
            units.append(make_unit(
                document_key, title, metadata, chapter, section, article_no, article_index,
                paragraph_no, None, text, "", block,
            ))

    quality = check_legal_quality(units, metadata)
    return {
        "schema_version": "1.0.0",
        "legal_document": {
            "document_key": document_key,
            "title": title,
            "source_title": metadata.get("title"),
            "issuer": metadata.get("issuer"),
            "promulgation_date": metadata.get("promulgation_date"),
            "revision_date": metadata.get("revision_date"),
            "effective_date": metadata.get("effective_date"),
            "expiry_date": metadata.get("expiry_date"),
            "status": metadata.get("status", "unknown"),
            "applicable_region": metadata.get("applicable_region", "全国"),
            "source_file": document.get("source_file"),
            "parser": document.get("parser"),
        },
        "units": units,
        "quality": quality,
    }


def make_unit(
    document_key: str, title: str, metadata: dict[str, Any], chapter: str, section: str,
    article_no: str, article_index: int | None, paragraph_no: int, item_no: str | None,
    text: str, parent_paragraph: str, block: dict[str, Any],
) -> dict[str, Any]:
    """构建单个法规单元；项的检索文本始终包含所属款的引导语。"""
    suffix = f"-I{chinese_number(item_no):02d}" if item_no else ""
    stable_article = f"A{article_index:04d}" if article_index is not None else hashlib.sha1(article_no.encode()).hexdigest()[:6]
    heading = [value for value in (chapter, section, article_no) if value]
    context = " ".join(value for value in (*heading, parent_paragraph, f"（{item_no}）" if item_no else "", text) if value)
    return {
        "legal_unit_id": f"LAW-{document_key}-{stable_article}-P{paragraph_no:02d}{suffix}",
        "unit_type": "item" if item_no else "paragraph",
        "document_title": title,
        "chapter": chapter or None,
        "section": section or None,
        "article_no": article_no,
        "article_index": article_index,
        "paragraph_no": paragraph_no,
        "item_no": item_no,
        "text": text,
        "parent_context": parent_paragraph or None,
        "search_text": context,
        "topics": dictionary_topics(context),
        "references": sorted(set(match.replace(" ", "") for match in REFERENCE_RE.findall(text))),
        "effective_date": metadata.get("effective_date"),
        "status": metadata.get("status", "unknown"),
        "evidence": [{
            "block_id": block.get("block_id"),
            "page_no": block.get("page_no"),
            "bbox": block.get("bbox"),
            "quote": normalize_legal_text(block.get("text")),
        }],
    }


def check_legal_quality(units: list[dict[str, Any]], metadata: dict[str, Any]) -> dict[str, Any]:
    """检查条号连续性、重复ID和证据缺失，决定是否可用于检索。"""
    issues = []
    article_indexes = sorted({unit["article_index"] for unit in units if unit.get("article_index") is not None})
    if article_indexes:
        missing = sorted(set(range(article_indexes[0], article_indexes[-1] + 1)) - set(article_indexes))
        if missing:
            issues.append({"code": "MISSING_ARTICLES", "message": f"条号可能缺失：{missing}"})
    ids = [unit["legal_unit_id"] for unit in units]
    if len(ids) != len(set(ids)):
        issues.append({"code": "DUPLICATE_UNIT_ID", "message": "存在重复法规单元ID"})
    if any(not unit.get("evidence") for unit in units):
        issues.append({"code": "MISSING_EVIDENCE", "message": "存在无原文证据的法规单元"})
    if not units:
        issues.append({"code": "NO_UNITS", "message": "未识别到任何法规条款"})
    if metadata.get("status", "unknown") == "unknown":
        issues.append({"code": "STATUS_UNCONFIRMED", "message": "法规效力状态尚未由知识治理人员确认"})
    if not metadata.get("effective_date"):
        issues.append({"code": "EFFECTIVE_DATE_MISSING", "message": "法规生效日期尚未确认"})
    blocking = {"DUPLICATE_UNIT_ID", "NO_UNITS"}
    metadata_pending = {"STATUS_UNCONFIRMED", "EFFECTIVE_DATE_MISSING"}
    codes = {issue["code"] for issue in issues}
    return {
        "status": "needs_review" if codes & blocking else "needs_metadata" if codes & metadata_pending else "reviewable",
        "structure_status": "reviewable" if units and not codes & blocking else "needs_review",
        "unit_count": len(units),
        "article_count": len(article_indexes),
        "first_article": article_indexes[0] if article_indexes else None,
        "last_article": article_indexes[-1] if article_indexes else None,
        "issues": issues,
    }


def infer_title(document: dict[str, Any]) -> str:
    """优先使用正文首个非目录、非层级、非日期段落作为法规名称。"""
    texts = [normalize_legal_text(block.get("text")) for block in document.get("blocks", [])[:30]]
    for text in texts:
        match = re.search(r"根据《([^》]+)》.*制定本条例", text)
        if match:
            return f"{match.group(1)}实施条例"
    for text in texts:
        if text and text != "目录" and not CHAPTER_RE.match(text) and not re.match(r"^(第?[一二三四五六七八九十百千万0-9]+[条章节]|[（(][一二三四五六七八九十百千万0-9]+[）)])", text) and any(word in text for word in ("法", "条例", "规定", "办法")):
            return text
    return str(document.get("document_id") or "未命名法规")


def normalize_legal_text(value: Any) -> str:
    """统一全角空格和连续空白，不改变法规用词与标点。"""
    return re.sub(r"[\t\r\n　 ]+", " ", str(value or "")).strip()


def chinese_number(value: str | None) -> int:
    """把常见中文条号转为整数；数字原样转换。"""
    if not value:
        return 0
    if value.isdigit():
        return int(value)
    digits = {"〇": 0, "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    units = {"十": 10, "百": 100, "千": 1000}
    if not any(char in units for char in value):
        return int("".join(str(digits[char]) for char in value if char in digits) or 0)
    total = current = 0
    for char in value:
        if char in digits:
            current = digits[char]
        elif char in units:
            total += (current or 1) * units[char]
            current = 0
    return total + current
