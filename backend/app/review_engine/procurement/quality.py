"""采购文档的确定性解析质量门禁。"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from copy import deepcopy
from pathlib import Path
from typing import Any

from app.integrations.mineru import MinerUService
from ..runner import read_json, write_json
from .batching import table_rows, table_structure_status


NOISE_TYPES = {"header", "footer", "page_number", "noise"}
TEXT_TYPES = {"heading", "paragraph", "table", "image"}
TABLE_CORRUPTION_PATTERNS = {
    "suspected_inserted_character": re.compile(r"修截改"),
    "suspected_missing_character": re.compile(r"(?<!截)止时间|(?<!布)发方式"),
}


def table_content_quality_flags(block: dict[str, Any]) -> list[str]:
    """检测需要重试的表格损坏，不静默更正来源文本。"""
    rows = table_rows(block)
    text = "\n".join(cell for row in rows for cell in row.get("cells", [])) or str(block.get("text") or "")
    flags = [name for name, pattern in TABLE_CORRUPTION_PATTERNS.items() if pattern.search(text)]
    for row in rows:
        for cell in row.get("cells", []):
            markers = re.findall(r"[（(]\d+[）)]", cell)
            if len(cell) >= 80 and len(markers) >= 3 and "\n" not in cell:
                flags.append("dense_numbered_items_without_line_breaks")
                return sorted(set(flags))
    return sorted(set(flags))


class QualityGateError(RuntimeError):
    """当不可靠的解析结果不得进入业务理解阶段时抛出。"""

    def __init__(self, report: dict[str, Any]):
        super().__init__("解析结果不可靠，已阻止自动审查，请人工复核或重新解析")
        self.report = report


class QualityCheckService:
    """评估解析器输出，不作采购领域判断。"""

    def prepare(self, document: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """在任何 Block 进入业务审查前，重新标记确定性噪声。"""
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
        damaged_tables = []
        for block in blocks:
            flags = table_content_quality_flags(block) if block.get("block_type") == "table" else []
            if flags:
                damaged_tables.append({"block_id": block.get("block_id"), "flags": flags})
        self._issue(
            bool(damaged_tables), issues, "TABLE_CONTENT_QUALITY", "error",
            "表格疑似存在错字、缺字或条目换行丢失，禁止直接形成自动结论",
            block_ids=[item["block_id"] for item in damaged_tables[:50]],
            details=damaged_tables[:50], review_route="review_finding",
        )
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
        """隔离不可靠的 Block，并在正常审查结果中展示。"""
        by_id = {block.get("block_id"): block for block in document.get("blocks", [])}
        actions = list(report.get("actions", []))
        for issue in report.get("issues", []):
            if issue.get("severity") == "error":
                issue["original_severity"] = "error"
                issue["severity"] = "warning"
                issue["review_route"] = "review_finding"
            if issue.get("code") not in {"TABLE_STRUCTURE", "TABLE_CONTENT_QUALITY"}:
                continue
            for block_id in issue.get("block_ids", []):
                block = by_id.get(block_id)
                if not block or block.get("block_type") == "noise":
                    continue
                if issue.get("code") == "TABLE_CONTENT_QUALITY" and self._retain_structured_table(block):
                    block["quality_reason"] = "table_content_degraded"
                    block["quality_flags"] = table_content_quality_flags(block)
                    block["review_route"] = "review_finding"
                    actions.append({
                        "block_id": block_id,
                        "action": "retained_with_quality_warning",
                        "reason": "table_content_degraded",
                        "route": "review_finding",
                    })
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
            "TABLE_CONTENT_QUALITY": "部分表格文字或换行无法可靠判断",
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
    def _retain_structured_table(block: dict[str, Any]) -> bool:
        """保留结构上可用的表格；内容警告继续附着在表格上。"""
        source = block.get("source") or {}
        return bool(
            isinstance(source, dict)
            and str(source.get("table_html") or "").strip()
            and table_structure_status(block) == "structured"
            and len(table_rows(block)) >= 2
        )

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


def table_retry_ranges(document: dict[str, Any], report: dict[str, Any]) -> list[tuple[int, int]]:
    """返回损坏表格附近带上下文的小范围页面区间。"""
    by_id = {block.get("block_id"): block for block in document.get("blocks", [])}
    bad_pages = sorted({
        int(by_id[block_id].get("page_no") or 0)
        for issue in report.get("issues", [])
        if issue.get("code") in {"TABLE_STRUCTURE", "TABLE_CONTENT_QUALITY", "EMPTY_TABLE_PLACEHOLDER"}
        for block_id in issue.get("block_ids", []) if block_id in by_id and by_id[block_id].get("page_no")
    })
    if not bad_pages:
        raise ValueError("表格重解析缺少可定位页码")
    max_page = max((int(block.get("page_no") or 0) for block in document.get("blocks", [])), default=max(bad_pages))
    groups: list[list[int]] = []
    for page in bad_pages:
        if not groups or page > groups[-1][-1] + 1:
            groups.append([page])
        else:
            groups[-1].append(page)
    padded = [(max(1, group[0] - 1), min(max_page, group[-1] + 1)) for group in groups]
    merged: list[tuple[int, int]] = []
    for start, end in padded:
        if merged and start <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def table_direct_ocr_pages(document: dict[str, Any], report: dict[str, Any]) -> set[int]:
    """返回文本缺失或损坏、无需先经过 Hybrid 的页面。"""
    by_id = {block.get("block_id"): block for block in document.get("blocks", [])}
    return {
        int(by_id[block_id].get("page_no") or 0)
        for issue in report.get("issues", [])
        if issue.get("code") in {"EMPTY_TABLE_PLACEHOLDER", "TABLE_CONTENT_QUALITY"}
        for block_id in issue.get("block_ids", [])
        if block_id in by_id and by_id[block_id].get("page_no")
    }


def supplement_damaged_table_text(
    mineru: MinerUService, document: dict[str, Any], source: Path, report: dict[str, Any],
) -> None:
    """仅为结构不可用的表格补充原生文本，避免与可用表格重复。"""
    by_id = {block.get("block_id"): block for block in document.get("blocks", [])}
    pages: set[int] = set()
    for issue in report.get("issues", []):
        if issue.get("code") != "TABLE_CONTENT_QUALITY":
            continue
        for block_id in issue.get("block_ids", []):
            block = by_id.get(block_id)
            if not block or not block.get("page_no"):
                continue
            # Content warnings (错字、缺字、换行丢失) should stay attached to
            # a structurally valid table. Adding native PDF text here creates a
            # second paragraph copy of the same table in logical units.
            if (
                issue.get("code") == "TABLE_CONTENT_QUALITY"
                and isinstance(block.get("source"), dict)
                and str(block["source"].get("table_html") or "").strip()
                and table_structure_status(block) == "structured"
                and len(table_rows(block)) >= 2
            ):
                continue
            pages.add(int(block["page_no"]))
    if pages:
        mineru.supplement_native_pdf_pages(
            document, source, pages,
            reason="table_content_recovery",
        )


def _source_fingerprint(source: Path) -> str:
    """用文件身份（名称+大小+mtime）做重试记忆的键；文件一变就作废。"""
    try:
        stat = source.stat()
        return f"{source.name}|{stat.st_size}|{stat.st_mtime_ns}"
    except OSError:
        return str(source.resolve())


def _load_retry_memo(memo_path: Path | None) -> dict[str, str]:
    """读取跨运行的重试记忆；缺失或损坏时按空处理，最坏只是重试重跑一次。"""
    if not memo_path or not memo_path.is_file():
        return {}
    try:
        data = read_json(memo_path)
        return {key: str(value) for key, value in data.items()} if isinstance(data, dict) else {}
    except Exception:
        return {}


def retry_table_ranges(
    original: dict[str, Any], source: Path, output_dir: Path, role: str,
    ranges: list[tuple[int, int]], direct_ocr_pages: set[int], mineru: MinerUService,
    retry_backend: str, retry_effort: str,
    memo_path: Path | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """将文本缺失直接转给 OCR；Hybrid 仅用于结构性失败。

    memo_path 指向跨运行的记忆文件：同一文件、同一区间、同一策略一旦被
    证明得分更差（rejected），后续运行直接跳过，不再重复无意义的高代价调用。
    """
    reparsed = original
    attempts: list[dict[str, Any]] = []
    last_error: Exception | None = None
    successful_calls = 0
    memo = _load_retry_memo(memo_path)
    fingerprint = _source_fingerprint(source)
    for start_page, end_page in ranges:
        best = reparsed
        best_score = _table_range_score(best, start_page, end_page)
        direct_ocr = any(start_page <= page <= end_page for page in direct_ocr_pages)
        strategies = [("ocr", mineru.backend, "ocr", "high")] if direct_ocr else [
            ("hybrid", retry_backend, "auto", retry_effort),
            ("ocr", mineru.backend, "ocr", "high"),
        ]
        for label, backend, parse_method, effort in strategies:
            memo_key = f"{fingerprint}::{start_page}-{end_page}::{label}"
            if memo.get(memo_key) == "rejected":
                attempts.append({
                    "pages": [start_page, end_page], "strategy": label, "backend": backend,
                    "parse_method": parse_method, "effort": effort, "status": "skipped",
                    "reason": "previous_retry_no_improvement",
                })
                continue
            try:
                partial = mineru.parse(
                    source, output_dir / f"{label}_pages_{start_page}_{end_page}", role,
                    parse_method=parse_method, backend=backend, effort=effort,
                    start_page_id=start_page - 1, end_page_id=end_page - 1,
                )
                successful_calls += 1
                candidate = _merge_page_retry(reparsed, partial, start_page, end_page, role)
                score = _table_range_score(candidate, start_page, end_page)
                accepted = score > best_score
                # 只记 rejected：被接受的策略每次仍重跑，因为它的内容只存在于
                # 本轮的合并结果里，跨运行并不继承。
                memo[memo_key] = "rejected" if not accepted else "accepted"
                attempts.append({
                    "pages": [start_page, end_page], "strategy": label, "backend": backend,
                    "parse_method": parse_method, "effort": effort, "status": "completed",
                    "score": score, "accepted": accepted,
                })
                if accepted:
                    best, best_score = candidate, score
                if not _table_range_needs_retry(best, start_page, end_page):
                    break
            except Exception as exc:
                last_error = exc
                attempts.append({
                    "pages": [start_page, end_page], "strategy": label, "backend": backend,
                    "parse_method": parse_method, "effort": effort, "status": "failed",
                    "error_type": type(exc).__name__,
                })
        reparsed = best
    if memo_path:
        try:
            write_json(memo_path, memo)
        except Exception:
            pass
    if not successful_calls and last_error:
        raise last_error
    return reparsed, attempts


def _table_range_needs_retry(document: dict[str, Any], start_page: int, end_page: int) -> bool:
    blocks = [
        block for block in document.get("blocks", [])
        if start_page <= int(block.get("page_no") or 0) <= end_page
    ]
    table_blocks = [
        block for block in blocks
        if block.get("block_type") == "table" or block.get("original_block_type") == "table"
    ]
    if any(table_structure_status(block) == "unreliable" or table_content_quality_flags(block) for block in table_blocks):
        return True
    page_chars = {
        page: sum(
            len(str(block.get("text") or "").strip()) for block in blocks
            if int(block.get("page_no") or 0) == page
            and block.get("block_type") not in {"header", "footer", "page_number", "noise"}
        )
        for page in range(start_page, end_page + 1)
    }
    return not page_chars or min(page_chars.values()) < 1


def _table_range_score(document: dict[str, Any], start_page: int, end_page: int) -> int:
    blocks = [
        block for block in document.get("blocks", [])
        if start_page <= int(block.get("page_no") or 0) <= end_page
    ]
    page_chars = {
        page: sum(
            len(str(block.get("text") or "").strip()) for block in blocks
            if int(block.get("page_no") or 0) == page
            and block.get("block_type") not in {"header", "footer", "page_number", "noise"}
        )
        for page in range(start_page, end_page + 1)
    }
    body_chars = sum(page_chars.values())
    empty_page_penalty = 50 * sum(value < 1 for value in page_chars.values())
    if body_chars == 0:
        return -100
    score = min(20, body_chars // 100) - empty_page_penalty
    for block in blocks:
        if block.get("block_type") != "table" and block.get("original_block_type") != "table":
            continue
        status = table_structure_status(block)
        score += 30 if status == "structured" else 5 if status == "flat_text_limited" else -30
        score -= 15 * len(table_content_quality_flags(block))
    return score


def _merge_page_retry(
    original: dict[str, Any], partial: dict[str, Any], start_page: int, end_page: int, role: str
) -> dict[str, Any]:
    """只替换请求的页面，同时保留其他页面的初始 Pipeline 解析结果。"""
    replacements = [dict(block) for block in partial.get("blocks", [])]
    if not replacements:
        raise ValueError(f"Hybrid 未返回第 {start_page}-{end_page} 页内容")
    page_numbers = [int(block.get("page_no") or 0) for block in replacements]
    range_length = end_page - start_page + 1
    if not all(start_page <= page <= end_page for page in page_numbers) and all(
        1 <= page <= range_length for page in page_numbers
    ):
        for block in replacements:
            block["page_no"] = int(block.get("page_no") or 0) + start_page - 1
    replacements = [block for block in replacements if start_page <= int(block.get("page_no") or 0) <= end_page]
    if not replacements:
        raise ValueError(f"Hybrid 返回内容与第 {start_page}-{end_page} 页不匹配")

    kept = [
        dict(block) for block in original.get("blocks", [])
        if not start_page <= int(block.get("page_no") or 0) <= end_page
    ]
    for index, block in enumerate(replacements, start=1):
        raw_id = str(block.get("source_block_id") or block.get("block_id") or index).split(":")[-1]
        block["source_block_id"] = raw_id
        block["block_id"] = f"{role}:HYBRID-P{int(block.get('page_no') or 0):04d}-{index:04d}"
    blocks = sorted(
        [*kept, *replacements],
        key=lambda block: (int(block.get("page_no") or 0), int(block.get("reading_order") or 0)),
    )
    for index, block in enumerate(blocks, start=1):
        block["reading_order"] = index
    merged = {**original, "blocks": blocks}
    parser = dict(original.get("parser") or {})
    parser.setdefault("localized_retries", []).append({
        "backend": (partial.get("parser") or {}).get("backend"),
        "parse_method": (partial.get("parser") or {}).get("parse_method"),
        "effort": (partial.get("parser") or {}).get("effort"),
        "pages": [start_page, end_page],
    })
    merged["parser"] = parser
    merged.pop("quality_actions", None)
    return merged
