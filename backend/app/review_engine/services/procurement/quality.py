"""Deterministic parse-quality gate for procurement documents."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from copy import deepcopy
from typing import Any

from .batching import table_structure_status


NOISE_TYPES = {"header", "footer", "page_number", "noise"}
TEXT_TYPES = {"heading", "paragraph", "table", "image"}


class QualityGateError(RuntimeError):
    """Raised when an unreliable parse must not enter business understanding."""

    def __init__(self, report: dict[str, Any]):
        super().__init__("解析结果不可靠，已阻止自动审查，请人工复核或重新解析")
        self.report = report


class QualityCheckService:
    """Evaluate parser output without making procurement-domain judgments."""

    def prepare(self, document: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Relabel deterministic noise before any block can enter business review."""
        prepared = deepcopy(document)
        blocks = prepared.get("blocks", [])
        explicit_noise = Counter(
            self._normalize(block.get("text"))
            for block in blocks
            if block.get("block_type") in NOISE_TYPES and self._normalize(block.get("text"))
        )
        edge_noise = self._repeated_edge_noise(blocks)
        actions: list[dict[str, Any]] = []
        for block in blocks:
            block_type = block.get("block_type")
            text = str(block.get("text") or "").strip()
            normalized = self._normalize(text)
            reason = None
            route = "excluded"
            if block_type in {"heading", "paragraph"} and not text:
                reason = "empty_text"
            elif block_type == "table" and not text and not str((block.get("source") or {}).get("table_html") or "").strip():
                reason, route = "empty_table_placeholder", "review_finding"
            elif block_type == "image" and not text:
                reason, route = "image_requires_ocr", "review_finding"
            elif block_type in {"heading", "paragraph"} and re.fullmatch(r"(?:第\s*)?\d+\s*(?:页|/\s*\d+)?", text):
                reason = "isolated_page_number"
            elif block_type in {"heading", "paragraph"} and normalized and (
                explicit_noise[normalized] >= 2 or normalized in edge_noise
            ):
                reason = "repeated_header_footer"
            if not reason:
                continue
            block["original_block_type"] = block_type
            block["block_type"] = "noise"
            block["review_route"] = route
            block["quality_reason"] = reason
            actions.append({"block_id": block.get("block_id"), "action": "excluded_from_review", "reason": reason, "route": route})
        prepared["quality_actions"] = actions
        return prepared, actions

    def check(self, document: dict[str, Any]) -> dict[str, Any]:
        blocks = document.get("blocks", [])
        pages: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for block in blocks:
            pages[int(block.get("page_no") or 0)].append(block)

        content = [block for block in blocks if block.get("block_type") in TEXT_TYPES]
        nonempty = [block for block in content if str(block.get("text") or "").strip()]
        text = "".join(str(block.get("text") or "") for block in nonempty)
        issues: list[dict[str, Any]] = []

        self._issue(not nonempty, issues, "NO_TEXT", "error", "没有可审查文本")
        empty_pages = [page for page, items in pages.items() if not any(str(item.get("text") or "").strip() for item in items if item.get("block_type") not in NOISE_TYPES)]
        self._issue(bool(empty_pages), issues, "EMPTY_PAGES", "warning", f"存在空页：{empty_pages[:20]}", pages=empty_pages)

        replacement_ratio = text.count("�") / max(len(text), 1)
        control_ratio = len(re.findall(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", text)) / max(len(text), 1)
        self._issue(replacement_ratio > 0.01 or control_ratio > 0.005, issues, "GARBLED_TEXT", "error", "乱码或控制字符比例过高")

        orders = [int(block.get("reading_order") or 0) for block in blocks]
        self._issue(len(orders) != len(set(orders)) or orders != sorted(orders), issues, "READING_ORDER", "warning", "阅读顺序重复或逆序")

        heading_levels = [int(block.get("heading_level") or len(block.get("heading_path") or []) or 1) for block in blocks if block.get("block_type") == "heading"]
        jumps = [(left, right) for left, right in zip(heading_levels, heading_levels[1:]) if right > left + 1]
        self._issue(bool(jumps), issues, "HEADING_LEVEL_JUMP", "warning", "标题层级存在跨级跳转", jumps=jumps[:20])

        noise_texts = Counter(str(block.get("text") or "").strip() for block in blocks if block.get("block_type") in NOISE_TYPES and str(block.get("text") or "").strip())
        mixed_noise = [value for value, count in noise_texts.items() if count >= 2 and any(value in str(block.get("text") or "") for block in content)]
        self._issue(bool(mixed_noise), issues, "NOISE_MIXED_IN_BODY", "warning", "重复页眉页脚疑似混入正文", samples=mixed_noise[:10])

        malformed_tables = [block.get("block_id") for block in blocks if block.get("block_type") == "table" and not self._valid_table(block)]
        self._issue(bool(malformed_tables), issues, "TABLE_STRUCTURE", "error", "表格缺少可靠结构，禁止按扁平文本形成自动结论", block_ids=malformed_tables[:50], review_route="review_finding")
        empty_tables = [block.get("block_id") for block in blocks if block.get("quality_reason") == "empty_table_placeholder"]
        self._issue(bool(empty_tables), issues, "EMPTY_TABLE_PLACEHOLDER", "warning", "页面存在未识别出内容的表格占位，已生成无法判断问题", block_ids=empty_tables[:50], review_route="review_finding")

        missing_captions = [
            block.get("block_id") for block in blocks
            if block.get("original_block_type") == "image" and block.get("quality_reason") == "image_requires_ocr"
        ]
        self._issue(bool(missing_captions), issues, "IMAGE_REQUIRES_OCR", "warning", "图片未识别出文本，已从自动证据中排除并生成无法判断问题", block_ids=missing_captions[:50], review_route="review_finding")

        confidences = [float(value) for block in blocks if (value := block.get("source", {}).get("ocr_confidence")) is not None]
        low_ocr = sum(value < 0.65 for value in confidences)
        self._issue(bool(confidences) and low_ocr / len(confidences) > 0.1, issues, "LOW_OCR_CONFIDENCE", "error", "低置信度 OCR Block 比例过高")
        parse_method = str(document.get("parser", {}).get("parse_method") or "not_assessed")
        if confidences:
            ocr_status = "low_confidence" if low_ocr / len(confidences) > 0.1 else "available"
        elif parse_method == "ocr":
            ocr_status = "unavailable"
            self._issue(bool(text), issues, "OCR_CONFIDENCE_UNAVAILABLE", "warning", "已执行 OCR，但解析器未返回置信度")
        else:
            ocr_status = "not_assessed"
            self._issue(bool(text), issues, "OCR_NOT_ASSESSED", "info", "当前解析结果无法判断是否需要 OCR，不以 0% 表示失败")

        attachment_refs = set(re.findall(r"附件\s*[一二三四五六七八九十\d]+", text))
        attachment_headings = {
            match.group(1)
            for block in blocks
            if (match := re.match(r"^\s*(附件\s*[一二三四五六七八九十\d]+)", str(block.get("text") or "")))
        }
        unresolved = sorted(attachment_refs - attachment_headings)
        critical = [reference for reference in unresolved if re.search(rf"(?:详见|见|按)\s*{re.escape(reference)}|{re.escape(reference)}[^。；]{{0,20}}(?:组成部分|为准|执行)", text)]
        ordinary = sorted(set(unresolved) - set(critical))
        self._issue(bool(critical), issues, "ATTACHMENT_REQUIRED_MISSING", "error", "正文依赖的附件未定位，相关内容无法自动判断", references=critical, review_route="review_finding")
        self._issue(bool(ordinary), issues, "ATTACHMENT_INCOMPLETE", "warning", "附件引用未找到对应附件标题，已生成待复核问题", references=ordinary, review_route="review_finding")

        errors = [issue for issue in issues if issue["severity"] == "error"]
        warnings = [issue for issue in issues if issue["severity"] == "warning"]
        if not nonempty or replacement_ratio > 0.08:
            status = "unreliable"
        elif errors:
            status = "retryable"
        elif warnings:
            status = "degraded"
        else:
            status = "passed"
        return {
            "status": status,
            "block_count": len(blocks),
            "nonempty_block_count": len(nonempty),
            "page_count": len(pages),
            "char_count": len(text),
            "replacement_ratio": round(replacement_ratio, 6),
            "ocr_confidence_coverage": round(len(confidences) / max(len(blocks), 1), 4),
            "ocr_status": ocr_status,
            "automatic_action_count": len(document.get("quality_actions", [])),
            "review_finding_block_ids": [
                action.get("block_id") for action in document.get("quality_actions", [])
                if action.get("route") == "review_finding"
            ],
            "issues": issues,
        }

    def degrade_to_review(self, document: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
        """Quarantine unreliable blocks and surface them in the normal review result."""
        by_id = {block.get("block_id"): block for block in document.get("blocks", [])}
        actions = list(report.get("actions", []))
        for issue in report.get("issues", []):
            if issue.get("severity") == "error":
                issue["original_severity"] = "error"
                issue["severity"] = "warning"
                issue["review_route"] = "review_finding"
            if issue.get("code") != "TABLE_STRUCTURE":
                continue
            for block_id in issue.get("block_ids", []):
                block = by_id.get(block_id)
                if not block or block.get("block_type") == "noise":
                    continue
                block["original_block_type"] = block.get("block_type")
                block["block_type"] = "noise"
                block["review_route"] = "review_finding"
                block["quality_reason"] = "table_structure_unreliable"
                actions.append({"block_id": block_id, "action": "excluded_from_review", "reason": "table_structure_unreliable", "route": "review_finding"})
        report["actions"] = actions
        report["automatic_action_count"] = len(actions)
        report["review_finding_block_ids"] = [action.get("block_id") for action in actions if action.get("route") == "review_finding"]
        report["quality_findings"] = self._quality_findings(document, report.get("issues", []))
        report["status"] = "degraded"
        return report

    @staticmethod
    def _quality_findings(document: dict[str, Any], issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
        by_id = {block.get("block_id"): block for block in document.get("blocks", [])}
        labels = {
            "TABLE_STRUCTURE": "部分表格结构无法可靠判断",
            "EMPTY_TABLE_PLACEHOLDER": "表格页面没有识别出可判断内容",
            "IMAGE_REQUIRES_OCR": "图片内容无法可靠判断",
            "ATTACHMENT_REQUIRED_MISSING": "正文依赖的附件无法定位",
            "ATTACHMENT_INCOMPLETE": "附件引用无法完整定位",
            "LOW_OCR_CONFIDENCE": "部分页面 OCR 结果无法可靠判断",
        }
        findings = []
        for issue in issues:
            code = issue.get("code")
            if code not in labels or issue.get("review_route") != "review_finding":
                continue
            block_ids = [value for value in issue.get("block_ids", []) if value in by_id]
            pages = sorted({int(by_id[value].get("page_no") or 0) for value in block_ids if by_id[value].get("page_no")})
            location = f"第{'、'.join(map(str, pages))}页" if pages else "相关附件或页面"
            findings.append({
                "finding_type": "parse_quality",
                "risk_level": "unknown",
                "title": labels[code],
                "description": f"{location}的解析证据不足，系统未据此自动给出合规结论。",
                "rationale": issue.get("message"),
                "recommendation": "在现有经办和主责复核环节查看原始页面后确认。",
                "evidence_block_ids": block_ids,
                "evidence_quotes": [],
                "rule_ids": [],
                "legal_unit_ids": [],
                "confidence": 0.0,
                "needs_human_confirmation": True,
                "quality_issue_code": code,
            })
        return findings

    @staticmethod
    def _valid_table(block: dict[str, Any]) -> bool:
        return table_structure_status(block) != "unreliable"

    @staticmethod
    def _normalize(value: Any) -> str:
        return re.sub(r"\s+", "", str(value or "")).lower()

    @classmethod
    def _repeated_edge_noise(cls, blocks: list[dict[str, Any]]) -> set[str]:
        pages: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for block in blocks:
            if str(block.get("text") or "").strip():
                pages[int(block.get("page_no") or 0)].append(block)
        candidates: dict[str, set[int]] = defaultdict(set)
        for page, items in pages.items():
            ordered = sorted(items, key=lambda item: int(item.get("reading_order") or 0))
            for block in [*ordered[:2], *ordered[-2:]]:
                text = str(block.get("text") or "").strip()
                normalized = cls._normalize(text)
                if block.get("block_type") == "paragraph" and 1 < len(text) <= 80:
                    candidates[normalized].add(page)
        return {value for value, page_numbers in candidates.items() if len(page_numbers) >= 3}

    @staticmethod
    def _issue(condition: bool, issues: list[dict[str, Any]], code: str, severity: str, message: str, **details: Any) -> None:
        if condition:
            issues.append({"code": code, "severity": severity, "message": message, **details})
