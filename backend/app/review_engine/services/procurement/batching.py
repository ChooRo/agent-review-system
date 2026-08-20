"""逻辑单元重建与审查批次契约。"""

from __future__ import annotations

import hashlib
import html as html_lib
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any


NOISE_TYPES = {"header", "footer", "page_number", "noise"}
ATTACHMENT = re.compile(r"^(附件|附录)\s*[一二三四五六七八九十A-Za-z\d]*")
ARTICLE = re.compile(r"^第[一二三四五六七八九十百千万\d]+条")
CLAUSE = re.compile(
    r"^(?:第[一二三四五六七八九十百千万\d]+条|"
    r"[一二三四五六七八九十百千万]+[、.]|"
    r"[（(][一二三四五六七八九十百千万\d]+[）)]|"
    r"\d+[.、)]|[①②③④⑤⑥⑦⑧⑨⑩])"
)
LEAD_IN = re.compile(r"(?:如下|下列|以下|包括|满足|要求)[：:]?$")
TABLE_AUXILIARY = re.compile(r"^(?:注|备注|说明|合计|总计|脚注)\s*[：:]?")
FIGURE_CAPTION = re.compile(r"^(?:图|附图)\s*[一二三四五六七八九十A-Za-z\d-]*")
REQUIREMENT_SIGNAL = re.compile(r"(?:须|应当?|不得|禁止|严禁|否决|无效|必须|不予|不接受)")
SUBITEM = re.compile(
    r"(?:^|[\n；;])\s*(?:[（(]?\d+[）).、]|[（(][一二三四五六七八九十]+[）)]|"
    r"[一二三四五六七八九十]+[、.])"
)
TOC_LINE = re.compile(r"(?:\.{2,}|…{2,}).*\d+\s*$")


def token_estimate(text: str) -> int:
    """不依赖分词器的保守中英文混合令牌估算。"""
    chinese = len(re.findall(r"[\u4e00-\u9fff]", text))
    other = max(len(text) - chinese, 0)
    return chinese + (other + 3) // 4


def table_business_row_count(block: dict[str, Any]) -> int:
    if block.get("role") not in {None, "primary"}:
        return 0
    fragment = block.get("table_fragment") or {}
    if fragment.get("row_start") and fragment.get("row_end"):
        return int(fragment["row_end"]) - int(fragment["row_start"]) + 1
    html = block.get("table_html")
    rows = table_rows({**block, "source": {"table_html": html}}) if html else []
    return sum(not row.get("is_header") for row in rows)


def candidate_estimate(block: dict[str, Any]) -> float:
    """调用模型前估算输出密度；上下文不会占用候选项预算。"""
    if block.get("role") not in {None, "primary"}:
        return 0.0
    block_type = str(block.get("type") or block.get("block_type") or "")
    if block_type in {"heading", "image"}:
        return 0.0
    if block_type == "table":
        return float(table_business_row_count(block) or 1)
    text = str(block.get("text") or "").strip()
    if not text:
        return 0.0
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) >= 5 and sum(bool(TOC_LINE.search(line)) for line in lines) >= len(lines) / 2:
        return 0.0
    subitems = len(SUBITEM.findall(text))
    if subitems:
        return float(subitems)
    return 1.0 if REQUIREMENT_SIGNAL.search(text) else 0.5


class _TableHTMLParser(HTMLParser):
    """无需额外依赖，从 MinerU 表格 HTML 中提取完整文本行。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[dict[str, Any]] = []
        self._row: list[dict[str, Any]] | None = None
        self._cell: list[str] | None = None
        self._cell_span = (1, 1)
        self._header = False
        self._rowspans: dict[int, tuple[int, str]] = {}
        self.malformed = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag == "tr":
            if self._row is not None:
                self.malformed = True
            self._row, self._header = [], False
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []
            values = dict(attrs)
            try:
                self._cell_span = (max(1, int(values.get("rowspan") or 1)), max(1, int(values.get("colspan") or 1)))
            except ValueError:
                self.malformed = True
                self._cell_span = (1, 1)
            self._header = self._header or tag == "th"
        elif tag in {"br", "p", "div", "li"} and self._cell is not None and self._cell:
            self._cell.append("\n")

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"td", "th"} and self._row is not None and self._cell is not None:
            lines = [" ".join(line.split()) for line in "".join(self._cell).splitlines()]
            self._row.append({
                "text": "\n".join(line for line in lines if line),
                "rowspan": self._cell_span[0],
                "colspan": self._cell_span[1],
            })
            self._cell = None
        elif tag == "tr" and self._row is not None:
            cells: dict[int, str] = {column: value for column, (_, value) in self._rowspans.items()}
            inherited_columns = sorted(cells)
            self._rowspans = {
                column: (remaining - 1, value)
                for column, (remaining, value) in self._rowspans.items()
                if remaining > 1
            }
            column = 0
            spans: list[dict[str, int]] = []
            for cell in self._row:
                while column in cells:
                    column += 1
                spans.append({"col": column, "rowspan": cell["rowspan"], "colspan": cell["colspan"]})
                for offset in range(cell["colspan"]):
                    value = cell["text"] if offset == 0 else ""
                    cells[column + offset] = value
                    if cell["rowspan"] > 1:
                        self._rowspans[column + offset] = (cell["rowspan"] - 1, value)
                column += cell["colspan"]
            expanded = [cells.get(index, "") for index in range(max(cells, default=-1) + 1)]
            if any(expanded):
                self.rows.append({
                    "cells": expanded,
                    "is_header": self._header,
                    "inherited_columns": inherited_columns,
                    "spans": spans,
                })
            self._row = None


def table_rows(block: dict[str, Any]) -> list[dict[str, Any]]:
    """返回完整的 MinerU HTML 行；绝不根据扁平化表格文本猜测行。"""
    source = block.get("source") or {}
    html = str(source.get("table_html") or "") if isinstance(source, dict) else ""
    text = str(block.get("text") or "").strip()
    if not html and text.lower().startswith("<table"):
        html = text
    if not html:
        return []
    parser = _TableHTMLParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        return []
    if parser.malformed or parser._row is not None or parser._cell is not None or "</table" not in html.lower():
        return []
    rows = parser.rows
    if not rows:
        return []
    widths = {len(row["cells"]) for row in rows}
    # 对于左侧列由跨行单元格继承的续行，MinerU 可能省略末尾空单元格。
    # 保留已知的表格网格宽度，不要因此丢弃整张表；这里不会臆造单元格文本。
    width = max(widths)
    for row in rows:
        row["cells"].extend([""] * (width - len(row["cells"])))
    if not any(row["is_header"] for row in rows):
        first = [cell for cell in rows[0]["cells"] if cell]
        header_words = ("条款", "名称", "内容", "序号", "项目", "要求", "评分", "分值", "参数")
        if first and max(map(len, first)) <= 40 and any(any(word in cell for word in header_words) for cell in first):
            rows[0]["is_header"] = True
    return [
        {
            **row,
            "text": " | ".join(row["cells"]),
            "block_id": block.get("block_id"),
            "row_index": index,
        }
        for index, row in enumerate(rows)
    ]


def table_business_view(block: dict[str, Any]) -> dict[str, Any] | None:
    """在保留行列来源信息的同时，构建供 AI 使用的紧凑记录。"""
    return table_business_view_from_rows(block, table_rows(block))


def table_business_view_from_rows(
    block: dict[str, Any], rows: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """根据已校验的行构建相同视图，使片段保留来源信息。"""
    headers = [row for row in rows if row.get("is_header")]
    records = [row for row in rows if not row.get("is_header")]
    if not headers or not records:
        return None
    width = len(rows[0]["cells"])
    columns: list[str] = []
    used: dict[str, int] = {}
    for column in range(width):
        parts: list[str] = []
        for row in headers:
            value = str(row["cells"][column] or "").strip()
            if value and value not in parts:
                parts.append(value)
        base = " / ".join(parts) or f"第{column + 1}列"
        used[base] = used.get(base, 0) + 1
        columns.append(base if used[base] == 1 else f"{base}_{used[base]}")
    has_spans = any(
        cell.get("rowspan", 1) > 1 or cell.get("colspan", 1) > 1
        for row in rows for cell in row.get("spans", [])
    )
    confidence = 0.8 if has_spans else 0.95
    return {
        "source": {
            "block_id": block.get("block_id"),
            "page": block.get("page_no") or block.get("page"),
        },
        "columns": columns,
        "records": [
            {
                "row": row["row_index"],
                "values": {columns[index]: value for index, value in enumerate(row["cells"])},
                **({"inherited_columns": row["inherited_columns"]} if row.get("inherited_columns") else {}),
                **({"spans": [cell for cell in row.get("spans", [])
                               if cell.get("rowspan", 1) > 1 or cell.get("colspan", 1) > 1]}
                   if any(cell.get("rowspan", 1) > 1 or cell.get("colspan", 1) > 1
                          for cell in row.get("spans", [])) else {}),
            }
            for row in records
        ],
        "structure_confidence": confidence,
    }


def table_structure_status(block: dict[str, Any]) -> str:
    """对表格输入进行分类，不把密集的扁平文本冒充为结构化内容。"""
    source = block.get("source") or {}
    html = str(source.get("table_html") or "") if isinstance(source, dict) else ""
    text = str(block.get("text") or "").strip()
    if html:
        return "structured" if table_rows(block) else "unreliable"
    if text.lower().startswith("<table"):
        return "structured" if table_rows({**block, "source": {**source, "table_html": text}}) else "unreliable"
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    separators = sum(bool(re.search(r"\s*[|\t]\s*|[^：:]{1,30}[：:]", line)) for line in lines)
    if text and token_estimate(text) <= 300 and len(lines) <= 12 and separators == len(lines):
        return "flat_text_limited"
    return "unreliable"


def rows_html(headers: list[dict[str, Any]], rows: list[dict[str, Any]]) -> str:
    def render(row: dict[str, Any], tag: str) -> str:
        cells = (html_lib.escape(str(cell)).replace("\n", "<br>") for cell in row["cells"])
        return "<tr>" + "".join(f"<{tag}>{cell}</{tag}>" for cell in cells) + "</tr>"
    return "<table>" + "".join(render(row, "th") for row in headers) + "".join(render(row, "td") for row in rows) + "</table>"


@dataclass(frozen=True)
class BatchBudget:
    model_tokens: int = 16_000
    output_tokens: int = 3_000
    safety_tokens: int = 1_000
    input_overhead_tokens: int = 2_000
    primary_block_limit: int = 25
    candidate_limit: int = 20
    table_row_limit: int = 16

    @property
    def input_tokens(self) -> int:
        return self.model_tokens - self.output_tokens - self.safety_tokens - self.input_overhead_tokens

    @property
    def max_table_rows(self) -> int:
        # 一个完整的采购候选项通常约占 150-200 个输出令牌。
        return max(1, min(self.table_row_limit, self.output_tokens // 180))


class LogicalUnitBuilder:
    """按来源顺序构建确定性单元；编号仅作为边界提示。"""

    def build(self, document: dict[str, Any]) -> dict[str, Any]:
        source_blocks = list(document.get("blocks", []))
        blocks = sorted(
            (block for block in source_blocks if self._reviewable(block)),
            key=self._order_key,
        )
        units: list[dict[str, Any]] = []
        current: dict[str, Any] | None = None
        attachment_level: int | None = None
        for block in blocks:
            text = str(block.get("text") or "").strip()
            block_type = block.get("block_type")
            is_heading = block_type == "heading"
            is_attachment_heading = bool(is_heading and ATTACHMENT.match(text))
            heading_level = self._heading_level(block)
            if is_attachment_heading:
                attachment_level = heading_level
            elif (
                is_heading
                and attachment_level is not None
                and (
                    heading_level < attachment_level
                    or (heading_level == attachment_level and not CLAUSE.match(text))
                )
            ):
                attachment_level = None
            in_attachment = attachment_level is not None
            unit_type = self._unit_type(block, in_attachment, current)
            heading_path = list(block.get("heading_path") or [])
            path_changed = bool(current and current["heading_path"] != heading_path)
            continues_table = self._continues_table(current, block, unit_type)
            starts = (
                current is None
                or is_attachment_heading
                or (in_attachment and current["unit_type"] != "attachment_unit")
                or (not in_attachment and is_heading)
                or (path_changed and not in_attachment)
                or (unit_type == "table_unit" and not continues_table)
                or (unit_type == "figure_unit" and current["unit_type"] != "figure_unit")
                or (current["unit_type"] in {"table_unit", "figure_unit", "attachment_unit"} and unit_type != current["unit_type"])
                or (unit_type == "clause_unit" and (current["unit_type"] != "clause_unit" or bool(ARTICLE.match(text))))
                or (unit_type == "paragraph_unit" and current["unit_type"] not in {"section_unit", "paragraph_unit", "clause_unit"})
            )
            if starts:
                current = self._new_unit(document, block, unit_type)
                units.append(current)
            else:
                self._append(current, block)

        block_owner = {block_id: unit["unit_id"] for unit in units for block_id in unit["primary_block_ids"]}
        self._bind_context(units, blocks, block_owner)
        return {
            "schema_version": 2,
            "document_id": document.get("document_id"),
            "document_role": document.get("document_role"),
            "units": units,
            "block_owner": block_owner,
            "excluded_block_ids": [block.get("block_id") for block in source_blocks if not self._reviewable(block)],
        }

    @staticmethod
    def _reviewable(block: dict[str, Any]) -> bool:
        block_type = block.get("block_type")
        if block_type in NOISE_TYPES:
            return False
        if block_type in {"heading", "paragraph", "image"} and not str(block.get("text") or "").strip():
            return False
        return True

    def _new_unit(self, document: dict[str, Any], block: dict[str, Any], unit_type: str) -> dict[str, Any]:
        identity = f"{document.get('document_id')}|{block.get('block_id')}|{unit_type}"
        entry = self._entry(block, "primary")
        parsed_rows = table_rows(block) if unit_type == "table_unit" else []
        text_tokens = token_estimate(str(block.get("text") or ""))
        row_tokens = sum(token_estimate(row["text"]) for row in parsed_rows)
        if unit_type == "clause_unit":
            entry["sequence_no"] = 1
        return {
            "unit_id": "LU-" + hashlib.sha1(identity.encode()).hexdigest()[:12],
            "unit_type": unit_type,
            "heading_path": list(block.get("heading_path") or []),
            "primary_block_ids": [block["block_id"]],
            "ordered_block_ids": [block["block_id"]],
            "context_block_ids": [],
            "blocks": [entry],
            "page_range": [int(block.get("page_no") or 0), int(block.get("page_no") or 0)],
            "token_estimate": max(text_tokens, row_tokens),
            "relation_mode": "sequential" if unit_type == "clause_unit" else "structural",
            "hierarchy_status": "not_inferred" if unit_type == "clause_unit" else "not_applicable",
            "table_signature": self._table_signature(block) if unit_type == "table_unit" else None,
            "table_rows": parsed_rows,
            "risks": self._table_risks(block, parsed_rows) if unit_type == "table_unit" else [],
        }

    def _append(self, unit: dict[str, Any], block: dict[str, Any]) -> None:
        entry = self._entry(block, "primary")
        if unit["unit_type"] == "clause_unit":
            entry["sequence_no"] = len(unit["primary_block_ids"]) + 1
        unit["primary_block_ids"].append(block["block_id"])
        unit["ordered_block_ids"].append(block["block_id"])
        unit["blocks"].append(entry)
        block_tokens = token_estimate(str(block.get("text") or ""))
        if unit["unit_type"] == "table_unit" and block.get("block_type") == "table":
            parsed_rows = table_rows(block)
            unit["table_rows"].extend(parsed_rows)
            unit["risks"].extend(self._table_risks(block, parsed_rows))
            block_tokens = max(block_tokens, sum(token_estimate(row["text"]) for row in parsed_rows))
        unit["token_estimate"] += block_tokens
        unit["page_range"][1] = max(unit["page_range"][1], int(block.get("page_no") or 0))

    @staticmethod
    def _entry(block: dict[str, Any], role: str) -> dict[str, Any]:
        text = str(block.get("text") or "")
        marker = CLAUSE.match(text.strip())
        entry = {"block_id": block.get("block_id"), "role": role, "type": block.get("block_type"), "page": block.get("page_no"), "text": text}
        source = block.get("source") or {}
        if block.get("block_type") == "table" and isinstance(source, dict) and source.get("table_html"):
            entry["table_html"] = source["table_html"]
        elif block.get("block_type") == "table" and text.strip().lower().startswith("<table"):
            entry["table_html"] = text
        if marker:
            entry["numbering_text"] = marker.group(0)
        return entry

    @staticmethod
    def _has_table_html(block: dict[str, Any]) -> bool:
        source = block.get("source") or {}
        return isinstance(source, dict) and bool(source.get("table_html"))

    @classmethod
    def _table_risks(cls, block: dict[str, Any], rows: list[dict[str, Any]]) -> list[str]:
        status = table_structure_status(block)
        if status == "unreliable":
            return ["table_html_unreliable"]
        if status == "flat_text_limited":
            return []
        if not rows:
            return ["table_html_unreliable"]
        return [] if any(row.get("is_header") for row in rows) else ["table_header_unresolved"]

    @staticmethod
    def _unit_type(block: dict[str, Any], attachment_scope: bool, current: dict[str, Any] | None) -> str:
        if attachment_scope:
            return "attachment_unit"
        if block.get("block_type") == "table":
            return "table_unit"
        if block.get("block_type") == "image":
            return "figure_unit"
        text = str(block.get("text") or "").strip()
        if current and current["unit_type"] == "table_unit" and TABLE_AUXILIARY.match(text):
            return "table_unit"
        if current and current["unit_type"] == "figure_unit" and FIGURE_CAPTION.match(text):
            return "figure_unit"
        if CLAUSE.search(text):
            return "clause_unit"
        if block.get("block_type") == "heading":
            return "section_unit"
        return "paragraph_unit"

    def _bind_context(self, units: list[dict[str, Any]], blocks: list[dict[str, Any]], block_owner: dict[str, str]) -> None:
        by_id = {block["block_id"]: block for block in blocks}
        positions = {block["block_id"]: index for index, block in enumerate(blocks)}
        last_heading: str | None = None
        for unit in units:
            first = by_id[unit["primary_block_ids"][0]]
            if first.get("block_type") == "heading":
                last_heading = first["block_id"]
                continue
            if last_heading and last_heading not in unit["primary_block_ids"]:
                self._add_context(unit, by_id[last_heading])
            if unit["unit_type"] == "clause_unit":
                previous_index = positions[first["block_id"]] - 1
                if previous_index >= 0:
                    previous = blocks[previous_index]
                    if block_owner.get(previous["block_id"]) != unit["unit_id"] and LEAD_IN.search(str(previous.get("text") or "").strip()):
                        self._add_context(unit, previous)

    def _add_context(self, unit: dict[str, Any], block: dict[str, Any]) -> None:
        block_id = block["block_id"]
        if block_id in unit["context_block_ids"] or block_id in unit["primary_block_ids"]:
            return
        unit["context_block_ids"].append(block_id)
        first_primary = next(
            (index for index, entry in enumerate(unit["blocks"]) if entry["role"] == "primary"),
            len(unit["blocks"]),
        )
        unit["blocks"].insert(first_primary, self._entry(block, "context"))
        unit["token_estimate"] += token_estimate(str(block.get("text") or ""))

    @staticmethod
    def _heading_level(block: dict[str, Any]) -> int:
        try:
            return int(block.get("heading_level") or len(block.get("heading_path") or []) or 1)
        except (TypeError, ValueError):
            return 1

    @staticmethod
    def _order_key(block: dict[str, Any]) -> tuple[float, int, float, float]:
        try:
            reading_order = float(block.get("reading_order"))
        except (TypeError, ValueError):
            reading_order = float("inf")
        bbox = block.get("bbox") or [0, 0, 0, 0]
        if isinstance(bbox, dict):
            x, y = float(bbox.get("x0") or 0), float(bbox.get("y0") or 0)
        else:
            coordinates = list(bbox) + [0, 0]
            x, y = float(coordinates[0] or 0), float(coordinates[1] or 0)
        return reading_order, int(block.get("page_no") or 0), y, x

    @staticmethod
    def _table_signature(block: dict[str, Any]) -> str:
        source = block.get("source") or {}
        html = str(source.get("table_html") or "") if isinstance(source, dict) else ""
        first_row = re.search(r"<tr[^>]*>(.*?)</tr>", html, flags=re.IGNORECASE | re.DOTALL)
        value = first_row.group(1) if first_row else str(block.get("text") or "").splitlines()[0:1]
        return re.sub(r"[\W_]+", "", "".join(value) if isinstance(value, list) else value).lower()

    def _continues_table(self, current: dict[str, Any] | None, block: dict[str, Any], unit_type: str) -> bool:
        if not current or unit_type != "table_unit" or current["unit_type"] != "table_unit":
            return False
        if block.get("block_type") != "table":
            return True
        page = int(block.get("page_no") or 0)
        same_path = current["heading_path"] == list(block.get("heading_path") or [])
        signature = self._table_signature(block)
        return bool(
            same_path
            and page == current["page_range"][1] + 1
            and signature
            and signature == current.get("table_signature")
        )


class BatchAssembler:
    """在模型输入预算内装入完整的逻辑单元。"""

    def __init__(self, budget: BatchBudget | None = None):
        self.budget = budget or BatchBudget()

    def assemble(self, manifest: dict[str, Any]) -> dict[str, Any]:
        batches: list[dict[str, Any]] = []
        current: list[dict[str, Any]] = []
        for unit in manifest.get("units", []):
            boundary = bool(current) and (
                unit.get("unit_type") == "attachment_unit"
                or current[-1].get("unit_type") == "attachment_unit"
            )
            if boundary:
                batches.append(self._batch(len(batches) + 1, current))
                current = []
            data_row_count = sum(not row.get("is_header") for row in unit.get("table_rows", []))
            if unit.get("unit_type") == "table_unit" and data_row_count > self.budget.max_table_rows:
                if current:
                    batches.append(self._batch(len(batches) + 1, current))
                    current = []
                split = self._split_table_unit(unit, len(batches) + 1)
                if split:
                    batches.extend(split)
                    continue
            if not self._fits([unit]):
                if current:
                    batches.append(self._batch(len(batches) + 1, current))
                    current = []
                batches.extend(self._split_oversized(unit, len(batches) + 1))
                continue
            if current and not self._fits([*current, unit]):
                batches.append(self._batch(len(batches) + 1, current))
                current = []
            current.append(unit)
        if current:
            batches.append(self._batch(len(batches) + 1, current))
        return {
            "schema_version": 3,
            "document_id": manifest.get("document_id"),
            "document_role": manifest.get("document_role"),
            "budget": {
                "model_tokens": self.budget.model_tokens,
                "output_tokens": self.budget.output_tokens,
                "safety_tokens": self.budget.safety_tokens,
                "input_overhead_tokens": self.budget.input_overhead_tokens,
                "input_tokens": self.budget.input_tokens,
                "primary_block_limit": self.budget.primary_block_limit,
                "candidate_limit": self.budget.candidate_limit,
                "table_row_limit": self.budget.max_table_rows,
            },
            "batches": batches,
        }

    def _fits(self, units: list[dict[str, Any]]) -> bool:
        batch = self._batch(0, units)
        return (
            batch["token_estimate"] <= self.budget.input_tokens
            and batch["primary_block_count"] <= self.budget.primary_block_limit
            and batch["candidate_estimate"] <= self.budget.candidate_limit
            and batch["table_row_count"] <= self.budget.max_table_rows
        )

    def _batch(self, number: int, units: list[dict[str, Any]]) -> dict[str, Any]:
        blocks, seen = [], set()
        for unit in units:
            for block in unit.get("blocks", []):
                entry = dict(block)
                if entry["block_id"] in seen and entry["role"] == "context":
                    entry["role"] = "repeated_context"
                seen.add(entry["block_id"])
                blocks.append(entry)
        primary = [block["block_id"] for block in blocks if block["role"] == "primary"]
        logical_units = [
            {
                "unit_id": unit["unit_id"],
                "unit_type": unit["unit_type"],
                "relation_mode": unit.get("relation_mode"),
                "hierarchy_status": unit.get("hierarchy_status"),
                "primary_block_ids": unit.get("primary_block_ids", []),
            }
            for unit in units
        ]
        unique_primary = set(primary)
        table_rows_count = sum(
            table_business_row_count(block) for block in blocks
            if str(block.get("type") or "") == "table"
        )
        return {
            "batch_no": number,
            "purpose": "procurement_understanding",
            "coverage_strategy": "complete_logical_units",
            "unit_ids": [unit["unit_id"] for unit in units],
            "logical_units": logical_units,
            "primary_block_ids": primary,
            "blocks": blocks,
            "token_estimate": sum(token_estimate(str(block.get("text") or "")) for block in blocks),
            "primary_block_count": len(unique_primary),
            "candidate_estimate": round(sum(candidate_estimate(block) for block in blocks), 1),
            "table_row_count": table_rows_count,
        }

    def _split_oversized(self, unit: dict[str, Any], start: int) -> list[dict[str, Any]]:
        """只在 Block 边界拆分；无法拆分的超大 Block 属于硬校验错误。"""
        if unit.get("unit_type") == "table_unit":
            table_batches = self._split_table_unit(unit, start)
            if table_batches:
                return table_batches
        result, group = [], []
        for block in unit.get("blocks", []):
            single_unit = {**unit, "blocks": [block], "primary_block_ids": [block["block_id"]] if block["role"] == "primary" else []}
            if not self._fits([single_unit]):
                result.append({**self._batch(start + len(result), [{**unit, "blocks": [block], "primary_block_ids": [block["block_id"]] if block["role"] == "primary" else []}]), "oversized": True})
                continue
            candidate_unit = {**unit, "blocks": [*group, block], "primary_block_ids": [item["block_id"] for item in [*group, block] if item["role"] == "primary"]}
            if group and not self._fits([candidate_unit]):
                result.append(self._batch(start + len(result), [{**unit, "blocks": group, "primary_block_ids": [item["block_id"] for item in group if item["role"] == "primary"]}]))
                group = []
            group.append(block)
        if group:
            result.append(self._batch(start + len(result), [{**unit, "blocks": group, "primary_block_ids": [item["block_id"] for item in group if item["role"] == "primary"]}]))
        return result

    def _split_table_unit(self, unit: dict[str, Any], start: int) -> list[dict[str, Any]]:
        """按完整 HTML 行拆分超大的 MinerU 表格 Block，并重复表头。"""
        table_blocks = [
            block for block in unit.get("blocks", [])
            if block.get("role") == "primary" and block.get("type") == "table"
        ]
        rows = unit.get("table_rows", [])
        if not table_blocks or not rows:
            return []
        if len(table_blocks) > 1:
            return self._split_multi_block_table_unit(unit, table_blocks, rows, start)
        table_block = table_blocks[0]
        if any(row.get("block_id") != table_block["block_id"] for row in rows):
            return []
        headers = [row for row in rows if row.get("is_header")]
        data_rows = [row for row in rows if not row.get("is_header")]
        if not headers or not data_rows:
            return []

        context = [dict(block) for block in unit.get("blocks", []) if block.get("role") != "primary"]
        auxiliary = [
            dict(block) for block in unit.get("blocks", [])
            if block.get("role") == "primary" and block.get("type") != "table"
        ]
        header_text = "\n".join(row["text"] for row in headers)
        fixed_size = sum(token_estimate(str(block.get("text") or "")) for block in context) + token_estimate(header_text)
        groups: list[list[dict[str, Any]]] = []
        current: list[dict[str, Any]] = []
        current_size = 0
        for row in data_rows:
            row_size = token_estimate(row["text"])
            if current and (
                fixed_size + current_size + row_size > self.budget.input_tokens
                or len(current) >= self.budget.max_table_rows
            ):
                groups.append(current)
                current, current_size = [], 0
            current.append(row)
            current_size += row_size
        if current:
            groups.append(current)

        batches: list[dict[str, Any]] = []
        row_offset = 0
        fragment_count = len(groups)
        for index, group in enumerate(groups, start=1):
            fragment_text = "\n".join([header_text, *(row["text"] for row in group)])
            fragment = {
                **table_block,
                "text": fragment_text,
                "table_html": rows_html(headers, group),
                "table_business": table_business_view_from_rows(table_block, [*headers, *group]),
                "table_fragment": {
                    "fragment_id": f"TF-{unit['unit_id']}-{index:03d}",
                    "fragment_no": index,
                    "fragment_count": fragment_count,
                    "row_start": row_offset + 1,
                    "row_end": row_offset + len(group),
                    "total_rows": len(data_rows),
                    "header_row_count": len(headers),
                    "complete_rows": True,
                },
            }
            row_offset += len(group)
            repeated_context = [
                {**block, "role": "repeated_context" if index > 1 else block["role"]}
                for block in context
            ]
            fragment_blocks = [*repeated_context, fragment]
            if index == fragment_count:
                candidate_size = sum(token_estimate(str(block.get("text") or "")) for block in [*fragment_blocks, *auxiliary])
                if candidate_size <= self.budget.input_tokens:
                    fragment_blocks.extend(auxiliary)
                    auxiliary = []
            fragment_unit = {
                **unit,
                "blocks": fragment_blocks,
                "primary_block_ids": [table_block["block_id"]],
            }
            batch = self._batch(start + len(batches), [fragment_unit])
            batch["coverage_strategy"] = "table_row_fragments"
            if batch["token_estimate"] > self.budget.input_tokens:
                batch["oversized"] = True
            batches.append(batch)

        if auxiliary:
            header_context = {
                **table_block,
                "role": "repeated_context",
                "text": header_text,
                "table_html": rows_html(headers, []),
                "table_fragment": {"header_only": True, "complete_rows": True},
            }
            auxiliary_unit = {
                **unit,
                "blocks": [*context, header_context, *auxiliary],
                "primary_block_ids": [block["block_id"] for block in auxiliary],
            }
            batch = self._batch(start + len(batches), [auxiliary_unit])
            batch["coverage_strategy"] = "table_row_fragments"
            if batch["token_estimate"] > self.budget.input_tokens:
                batch["oversized"] = True
            batches.append(batch)
        return batches

    def _split_multi_block_table_unit(
        self,
        unit: dict[str, Any],
        table_blocks: list[dict[str, Any]],
        rows: list[dict[str, Any]],
        start: int,
    ) -> list[dict[str, Any]]:
        """拆分重建后的跨页表格，同时保留每个来源 Block ID。"""
        context = [dict(block) for block in unit.get("blocks", []) if block.get("role") != "primary"]
        auxiliary = [
            dict(block) for block in unit.get("blocks", [])
            if block.get("role") == "primary" and block.get("type") != "table"
        ]
        batches: list[dict[str, Any]] = []
        previous_tail: list[dict[str, Any]] = []
        for table_index, table_block in enumerate(table_blocks):
            block_rows = [row for row in rows if row.get("block_id") == table_block["block_id"]]
            if not block_rows or not any(row.get("is_header") for row in block_rows):
                return []
            repeated_context = [
                {**block, "role": "repeated_context" if table_index or batches else block["role"]}
                for block in context
            ]
            if previous_tail:
                previous_headers = [row for row in rows if row.get("block_id") == table_blocks[table_index - 1]["block_id"] and row.get("is_header")]
                continuation = {
                    **table_blocks[table_index - 1],
                    "role": "repeated_context",
                    "text": "跨页续表上下文：\n" + "\n".join(row["text"] for row in previous_tail),
                    "table_html": rows_html(previous_headers, previous_tail),
                    "table_fragment": {
                        "continuation_context": True,
                        "source_block_id": table_blocks[table_index - 1]["block_id"],
                        "row_start": previous_tail[0]["row_index"],
                        "row_end": previous_tail[-1]["row_index"],
                    },
                }
                repeated_context.append(continuation)
            subunit = {
                **unit,
                "blocks": [*repeated_context, table_block],
                "primary_block_ids": [table_block["block_id"]],
                "table_rows": block_rows,
            }
            pieces = self._split_table_unit(subunit, start + len(batches))
            if not pieces:
                return []
            for piece in pieces:
                for block in piece.get("blocks", []):
                    fragment = block.get("table_fragment")
                    if block.get("block_id") == table_block["block_id"] and isinstance(fragment, dict):
                        fragment["fragment_id"] = (
                            f"TF-{unit['unit_id']}-{table_index + 1:03d}-{int(fragment.get('fragment_no') or 1):03d}"
                        )
            batches.extend(pieces)
            previous_tail = [row for row in block_rows if not row.get("is_header")][-2:]
        if auxiliary:
            last_table = table_blocks[-1]
            last_headers = [
                row for row in rows
                if row.get("block_id") == last_table["block_id"] and row.get("is_header")
            ]
            header_context = {
                **last_table,
                "role": "repeated_context",
                "text": "\n".join(row["text"] for row in last_headers),
                "table_html": rows_html(last_headers, []),
                "table_fragment": {"header_only": True, "complete_rows": True},
            }
            auxiliary_unit = {
                **unit,
                "blocks": [*context, header_context, *auxiliary],
                "primary_block_ids": [block["block_id"] for block in auxiliary],
                "table_rows": [],
            }
            batches.append(self._batch(start + len(batches), [auxiliary_unit]))
        return batches


class BatchValidator:
    """校验覆盖范围、证据身份、附件边界和令牌限制。"""

    def validate(self, logical: dict[str, Any], batches: dict[str, Any]) -> dict[str, Any]:
        issues: list[dict[str, Any]] = []
        expected = list(logical.get("block_owner", {}))
        primary = [block_id for batch in batches.get("batches", []) for block_id in batch.get("primary_block_ids", [])]
        missing = sorted(set(expected) - set(primary))
        duplicate_ids = {block_id for block_id in set(primary) if primary.count(block_id) != 1}
        attempted_fragment_ids = {
            block.get("block_id")
            for batch in batches.get("batches", [])
            for block in batch.get("blocks", [])
            if block.get("role") == "primary"
            and isinstance(block.get("table_fragment"), dict)
        }
        valid_fragment_ids = {
            block_id for block_id in duplicate_ids
            if self._valid_table_fragments(block_id, batches)
        }
        fragment_errors = sorted((duplicate_ids & attempted_fragment_ids) - valid_fragment_ids)
        duplicates = sorted(duplicate_ids - valid_fragment_ids - attempted_fragment_ids)
        if missing:
            issues.append({"code": "PRIMARY_COVERAGE_MISSING", "severity": "error", "block_ids": missing})
        if duplicates:
            issues.append({"code": "PRIMARY_COVERAGE_DUPLICATE", "severity": "error", "block_ids": duplicates})
        if fragment_errors:
            issues.append({"code": "TABLE_FRAGMENT_COVERAGE", "severity": "error", "block_ids": fragment_errors})
        known = set(expected)
        invalid = sorted({block.get("block_id") for batch in batches.get("batches", []) for block in batch.get("blocks", []) if block.get("block_id") not in known})
        if invalid:
            issues.append({"code": "UNKNOWN_EVIDENCE_ID", "severity": "error", "block_ids": invalid})
        limit = int(batches.get("budget", {}).get("input_tokens") or 0)
        oversized = [batch["batch_no"] for batch in batches.get("batches", []) if batch.get("token_estimate", 0) > limit or batch.get("oversized")]
        if oversized:
            issues.append({"code": "TOKEN_LIMIT", "severity": "error", "batch_nos": oversized})
        for field, budget_key, code in (
            ("primary_block_count", "primary_block_limit", "PRIMARY_BLOCK_LIMIT"),
            ("candidate_estimate", "candidate_limit", "CANDIDATE_ESTIMATE_LIMIT"),
            ("table_row_count", "table_row_limit", "TABLE_ROW_LIMIT"),
        ):
            field_limit = float(batches.get("budget", {}).get(budget_key) or 0)
            invalid_batches = [
                batch["batch_no"] for batch in batches.get("batches", [])
                if field_limit and float(batch.get(field) or 0) > field_limit
            ]
            if invalid_batches:
                issues.append({"code": code, "severity": "error", "batch_nos": invalid_batches})
        unreliable_tables = sorted({
            block_id
            for unit in logical.get("units", [])
            if "table_html_unreliable" in unit.get("risks", [])
            for block_id in unit.get("primary_block_ids", [])
        })
        if unreliable_tables:
            issues.append({"code": "TABLE_STRUCTURE_UNRELIABLE", "severity": "error", "block_ids": unreliable_tables})
        max_rows = max(1, int(batches.get("budget", {}).get("table_row_limit") or 0))
        unsplittable_dense_tables = sorted({
            block_id
            for unit in logical.get("units", [])
            if "table_header_unresolved" in unit.get("risks", [])
            and sum(not row.get("is_header") for row in unit.get("table_rows", [])) > max_rows
            for block_id in unit.get("primary_block_ids", [])
        })
        if unsplittable_dense_tables:
            issues.append({"code": "TABLE_HEADER_REQUIRED_FOR_SPLIT", "severity": "error", "block_ids": unsplittable_dense_tables})
        for batch in batches.get("batches", []):
            types = {unit["unit_type"] for unit in logical.get("units", []) if unit["unit_id"] in batch.get("unit_ids", [])}
            if "attachment_unit" in types and len(types) > 1:
                issues.append({"code": "ATTACHMENT_BOUNDARY", "severity": "error", "batch_no": batch["batch_no"]})
        return {"status": "failed" if any(issue["severity"] == "error" for issue in issues) else "passed", "issues": issues, "primary_block_count": len(set(primary)), "batch_count": len(batches.get("batches", []))}

    @staticmethod
    def _valid_table_fragments(block_id: str, batches: dict[str, Any]) -> bool:
        fragments = [
            block["table_fragment"]
            for batch in batches.get("batches", [])
            for block in batch.get("blocks", [])
            if block.get("block_id") == block_id
            and block.get("role") == "primary"
            and isinstance(block.get("table_fragment"), dict)
        ]
        if len(fragments) < 2:
            return False
        fragments.sort(key=lambda item: int(item.get("fragment_no") or 0))
        expected_count = len(fragments)
        expected_start = 1
        for index, fragment in enumerate(fragments, start=1):
            if (
                fragment.get("fragment_no") != index
                or fragment.get("fragment_count") != expected_count
                or fragment.get("row_start") != expected_start
                or not fragment.get("complete_rows")
            ):
                return False
            expected_start = int(fragment.get("row_end") or 0) + 1
        return expected_start - 1 == int(fragments[-1].get("total_rows") or 0)
