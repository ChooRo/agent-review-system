"""独立MinerU接入和统一Document JSON构建。"""

from __future__ import annotations

import json
import mimetypes
import shutil
import subprocess
import tempfile
import uuid
import zipfile
from pathlib import Path
from typing import Any

import httpx


class MinerUService:
    """解析原始业务文件，也接受MinerU content_list或统一Document JSON作为调试输入。"""

    def __init__(self, api_url: str = "http://127.0.0.1:8000", timeout_seconds: int = 900):
        self.api_url = api_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def parse(self, source: Path, output_dir: Path, role: str) -> dict[str, Any]:
        """将文件转换为统一Document JSON；JSON输入用于跳过耗时解析的调试。"""
        source = source.resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        if source.suffix.lower() == ".json":
            raw = json.loads(source.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and isinstance(raw.get("blocks"), list):
                document = raw
            else:
                document = adapt_content_list(raw, source.stem, role)
        else:
            parse_source = self._prepare_source(source, output_dir)
            content_list = self._call_api(parse_source, output_dir)
            raw = json.loads(content_list.read_text(encoding="utf-8"))
            document = adapt_content_list(raw, source.stem, role)
        document["document_role"] = role
        document["source_file"] = str(source)
        namespace_block_ids(document, role)
        return document

    def _prepare_source(self, source: Path, output_dir: Path) -> Path:
        """MinerU支持的PDF、DOCX和图片直接返回；旧DOC先转为固定版式PDF。"""
        suffix = source.suffix.lower()
        if suffix in {".pdf", ".docx", ".png", ".jpg", ".jpeg"}:
            return source
        if suffix != ".doc":
            raise ValueError(f"MVP仅支持PDF、DOC、DOCX、PNG、JPG或调试JSON：{source.name}")
        converter = shutil.which("soffice") or shutil.which("libreoffice")
        if not converter:
            raise RuntimeError("未找到LibreOffice/soffice，无法把旧版DOC转换为PDF")
        converted_dir = output_dir / "converted"
        converted_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [converter, "--headless", "--convert-to", "pdf", "--outdir", str(converted_dir), str(source)],
            check=True,
            timeout=300,
        )
        pdf = converted_dir / f"{source.stem}.pdf"
        if not pdf.is_file():
            raise RuntimeError(f"DOC转PDF后未找到输出：{pdf}")
        return pdf

    def _call_api(self, source: Path, output_dir: Path) -> Path:
        """调用MinerU /file_parse接口，安全解压ZIP并返回content_list。"""
        endpoint = f"{self.api_url}/file_parse"
        fields = {
            "backend": "pipeline",
            "parse_method": "auto",
            "lang_list": "ch",
            "table_enable": "true",
            "return_content_list": "true",
            "return_images": "true",
            "response_format_zip": "true",
        }
        mime = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
        with source.open("rb") as handle, httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(endpoint, data=fields, files={"files": (source.name, handle, mime)})
        response.raise_for_status()
        extract_dir = output_dir / "mineru"
        extract_dir.mkdir(parents=True, exist_ok=True)
        safe_extract_zip(response.content, extract_dir)
        candidates = sorted(extract_dir.rglob("*_content_list.json")) or sorted(
            extract_dir.rglob("*content_list*.json")
        )
        if not candidates:
            raise FileNotFoundError("MinerU结果中没有content_list.json")
        return candidates[0]


def adapt_content_list(raw: Any, document_id: str, role: str) -> dict[str, Any]:
    """把MinerU内容列表转换为统一Block，保留原文、类型、页码、坐标和阅读顺序。"""
    items = raw.get("content_list", raw.get("items", raw.get("blocks"))) if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        raise ValueError("MinerU content_list必须是数组")
    headings: list[str] = []
    blocks: list[dict[str, Any]] = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        source_type = str(item.get("type") or item.get("source_type") or "text").lower()
        block_type = {
            "title": "heading",
            "table": "table",
            "image": "image",
            "header": "header",
            "footer": "footer",
            "page_number": "page_number",
        }.get(source_type, "paragraph")
        if source_type == "text" and item.get("text_level") is not None:
            block_type = "heading"
        text = extract_text(item, source_type)
        if block_type == "heading" and text:
            level = int(item.get("text_level") or item.get("level") or 1)
            level = min(max(level, 1), 6)
            headings[level - 1 :] = [text]
        block_id = str(item.get("block_id") or item.get("id") or f"B-{index:05d}")
        blocks.append(
            {
                "block_id": block_id,
                "block_type": block_type,
                "heading_path": list(headings),
                "text": text,
                "page_no": int(item.get("page_idx", item.get("page_no", 0))) + (1 if "page_idx" in item else 0),
                "bbox": item.get("bbox") or item.get("poly") or item.get("position"),
                "reading_order": index,
                "source": {
                    "image_ref": item.get("img_path") or item.get("image_path"),
                    "table_html": item.get("table_body") or item.get("html"),
                },
            }
        )
    return {
        "document_id": document_id,
        "document_role": role,
        "parser": {"name": "mineru", "source": "content_list"},
        "blocks": blocks,
    }


def extract_text(item: dict[str, Any], source_type: str) -> str:
    """从不同MinerU条目中取得可供审查的文本。"""
    keys = ["text", "content"]
    if source_type == "table":
        keys = ["table_caption", "caption", "text", "table_body", "html"]
    elif source_type == "image":
        keys = ["img_caption", "caption", "text", "content"]
    values = [str(item.get(key)).strip() for key in keys if item.get(key)]
    return "\n".join(dict.fromkeys(values))


def namespace_block_ids(document: dict[str, Any], role: str) -> None:
    """给Block ID增加文档角色前缀，防止三份文件中的B-00001互相覆盖。"""
    prefix = f"{role}:"
    for block in document.get("blocks", []):
        raw_id = str(block.get("block_id") or uuid.uuid4().hex)
        block["source_block_id"] = raw_id.removeprefix(prefix)
        block["block_id"] = raw_id if raw_id.startswith(prefix) else prefix + raw_id


def safe_extract_zip(data: bytes, output_dir: Path) -> None:
    """安全解压MinerU ZIP，禁止路径穿越。"""
    with tempfile.TemporaryDirectory() as temp_dir:
        archive_path = Path(temp_dir) / f"{uuid.uuid4().hex}.zip"
        archive_path.write_bytes(data)
        with zipfile.ZipFile(archive_path) as archive:
            root = output_dir.resolve()
            for member in archive.infolist():
                destination = (root / member.filename).resolve()
                try:
                    destination.relative_to(root)
                except ValueError as exc:
                    raise ValueError(f"MinerU ZIP包含越界路径：{member.filename}") from exc
            archive.extractall(root)
