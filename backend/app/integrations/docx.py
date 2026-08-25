"""Native DOCX parsing through docx2python."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from docx2python import docx2python


class Docx2PythonService:
    """Parse editable DOCX files into the project's unified document shape."""

    def parse(self, source: Path, output_dir: Path, role: str) -> dict[str, Any]:
        from .mineru import namespace_block_ids

        source = source.resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        blocks: list[dict[str, Any]] = []
        with docx2python(source, output_dir / "images") as content:
            for table in content.body:
                if len(table) > 1 or any(len(row) > 1 for row in table):
                    text = "\n".join(
                        " | ".join(str(paragraph).strip() for cell in row for paragraph in cell if str(paragraph).strip())
                        for row in table
                    )
                    self._append_block(blocks, text, "table", "tbl")
                else:
                    for row in table:
                        for cell in row:
                            for paragraph in cell:
                                self._append_block(blocks, str(paragraph), "paragraph", "p")
        document = {
            "document_id": source.stem,
            "document_role": role,
            "source_file": str(source),
            "parser": {"name": "docx2python", "source": "native_docx"},
            "blocks": blocks,
        }
        namespace_block_ids(document, role)
        return document

    @staticmethod
    def _append_block(blocks: list[dict[str, Any]], raw_text: str, block_type: str, element: str) -> None:
        text = raw_text.strip()
        if not text:
            return
        index = len(blocks) + 1
        blocks.append({
            "block_id": f"B-{index:05d}",
            "block_type": block_type,
            "heading_path": [],
            "text": text,
            "page_no": None,
            "bbox": None,
            "reading_order": index,
            "heading_level": None,
            "source": {"docx_element": element},
        })
