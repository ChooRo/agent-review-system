"""独立MinerU接入和统一Document JSON构建。"""

from __future__ import annotations

import base64
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

from .docx import Docx2PythonService


class MinerUService:
    """解析原始业务文件，也接受MinerU content_list或统一Document JSON作为调试输入。"""

    def __init__(
        self,
        api_url: str = "http://127.0.0.1:8001",
        timeout_seconds: int = 900,
        ocr_config: dict[str, Any] | None = None,
        backend: str = "pipeline",
        effort: str = "medium",
    ):
        self.api_url = api_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.ocr_config = ocr_config or {}
        self.backend = backend
        self.effort = effort
        self.docx = Docx2PythonService()

    def parse(
        self,
        source: Path,
        output_dir: Path,
        role: str,
        parse_method: str = "auto",
        backend: str | None = None,
        effort: str | None = None,
        start_page_id: int | None = None,
        end_page_id: int | None = None,
    ) -> dict[str, Any]:
        """将文件转换为统一Document JSON；JSON输入用于跳过耗时解析的调试。"""
        source = source.resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        if source.suffix.lower() == ".docx":
            document = self.docx.parse(source, output_dir / "docx", role)
            document["document_role"] = role
            document["source_file"] = str(source)
            return document
        if source.suffix.lower() == ".json":
            raw = json.loads(source.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and isinstance(raw.get("blocks"), list):
                document = raw
            else:
                document = adapt_content_list(raw, source.stem, role)
        else:
            parse_source = self._prepare_source(source, output_dir)
            selected_backend = backend or self.backend
            selected_effort = effort or self.effort
            content_list = self._call_api(
                parse_source, output_dir, parse_method, selected_backend, selected_effort,
                start_page_id=start_page_id, end_page_id=end_page_id,
            )
            raw = json.loads(content_list.read_text(encoding="utf-8"))
            document = adapt_content_list(raw, source.stem, role)
            resolve_image_refs(document, content_list.parent, output_dir / "mineru")
            self._supplement_images(document)
            self._supplement_missing_pdf_pages(document, source)
        document["document_role"] = role
        document["source_file"] = str(source)
        parser = document.setdefault("parser", {})
        parser["parse_method"] = parse_method if source.suffix.lower() != ".json" else "not_assessed"
        parser["backend"] = (backend or self.backend) if source.suffix.lower() != ".json" else "not_assessed"
        parser["effort"] = (effort or self.effort) if source.suffix.lower() != ".json" else "not_assessed"
        if source.suffix.lower() != ".json" and start_page_id is not None:
            parser["page_range"] = [start_page_id, end_page_id]
        namespace_block_ids(document, role)
        return document

    def _supplement_images(self, document: dict[str, Any]) -> None:
        """对空图片 Block 使用可选的 OpenAI 兼容 DeepSeek-OCR 服务。"""
        api_url = str(self.ocr_config.get("api_url") or "").rstrip("/")
        model = str(self.ocr_config.get("model") or "")
        if not api_url or not model:
            return
        headers = {"Content-Type": "application/json"}
        if self.ocr_config.get("api_key"):
            headers["Authorization"] = f"Bearer {self.ocr_config['api_key']}"
        timeout = int(self.ocr_config.get("timeout_seconds") or 120)
        prompt = str(self.ocr_config.get("prompt") or "OCR this image.")
        for block in document.get("blocks", []):
            source = block.setdefault("source", {})
            image_path = Path(str(source.get("image_ref") or ""))
            if block.get("block_type") != "image" or str(block.get("text") or "").strip() or not image_path.is_file():
                continue
            mime = mimetypes.guess_type(image_path.name)[0] or "image/png"
            encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
            payload = {
                "model": model,
                "temperature": 0,
                "messages": [{"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}},
                    {"type": "text", "text": prompt},
                ]}],
            }
            try:
                response = None
                for attempt in range(2):
                    with httpx.Client(timeout=timeout, trust_env=False) as client:
                        response = client.post(f"{api_url}/chat/completions", headers=headers, json=payload)
                    if response.status_code not in {429, 500, 502, 503, 504} or attempt == 1:
                        break
                assert response is not None
                response.raise_for_status()
                content = response.json().get("choices", [{}])[0].get("message", {}).get("content", "")
                if isinstance(content, list):
                    content = "\n".join(str(item.get("text") or "") for item in content if isinstance(item, dict))
                raw_text = str(content or "").strip()
                text = raw_text
                for echo in (prompt, prompt.replace("<|grounding|>", "").strip()):
                    if echo and text.startswith(echo):
                        text = text[len(echo):].lstrip(" \r\n:：")
                block["text"] = text
                source["ocr_raw_text"] = raw_text
                source["ocr_provider"] = "deepseek_ocr"
                source["ocr_model"] = model
            except httpx.HTTPStatusError as exc:
                source["ocr_error"] = type(exc).__name__
                source["ocr_status_code"] = exc.response.status_code
                source["ocr_error_detail"] = exc.response.text[:500]
            except Exception as exc:
                source["ocr_error"] = type(exc).__name__

    def _supplement_missing_pdf_pages(self, document: dict[str, Any], source_file: Path) -> None:
        """当版面解析器丢失原本可读的页面时，保留 PDF 原生文本。"""
        self.supplement_native_pdf_pages(document, source_file, only_missing=True)

    def supplement_native_pdf_pages(
        self,
        document: dict[str, Any],
        source_file: Path,
        page_numbers: set[int] | None = None,
        *,
        only_missing: bool = False,
        reason: str = "missing_page_content",
    ) -> None:
        """将 PDF 原生文本层作为明确的非结构化兜底内容附加到文档中。"""
        if source_file.suffix.lower() != ".pdf":
            return
        pages = self._native_pdf_pages(source_file)
        if not pages:
            return
        blocks = document.get("blocks", [])
        content_pages = {
            int(block.get("page_no") or 0)
            for block in blocks
            if block.get("block_type") not in {"header", "footer", "page_number", "noise"}
            and str(block.get("text") or "").strip()
        }
        existing_fallback_pages = {
            int(block.get("page_no") or 0)
            for block in blocks
            if (block.get("source") or {}).get("parser_fallback") == "native_pdf_text"
        }
        for page_no, text in enumerate(pages, start=1):
            text = text.strip()
            if page_numbers is not None and page_no not in page_numbers:
                continue
            if page_no in existing_fallback_pages or (only_missing and page_no in content_pages) or len(text) < 40:
                continue
            blocks.append({
                "block_id": f"NATIVE-P{page_no:04d}",
                "block_type": "paragraph",
                "heading_path": [],
                "text": text,
                "page_no": page_no,
                "bbox": None,
                "reading_order": len(blocks) + 1,
                "heading_level": None,
                "source": {
                    "parser_fallback": "native_pdf_text",
                    "fallback_reason": reason,
                },
            })

    @staticmethod
    def _native_pdf_pages(source_file: Path) -> list[str]:
        """如果可用则使用 Poppler 提取 PDF 文本层；没有它时解析仍可工作。"""
        extractor = shutil.which("pdftotext")
        if not extractor:
            return []
        try:
            result = subprocess.run(
                [extractor, "-layout", "-enc", "UTF-8", str(source_file), "-"],
                check=True, capture_output=True, text=True, encoding="utf-8", timeout=120,
            )
        except (OSError, subprocess.SubprocessError, UnicodeError):
            return []
        return result.stdout.split("\f")[:-1] if result.stdout.endswith("\f") else result.stdout.split("\f")

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

    def _call_api(
        self,
        source: Path,
        output_dir: Path,
        parse_method: str = "auto",
        backend: str | None = None,
        effort: str | None = None,
        start_page_id: int | None = None,
        end_page_id: int | None = None,
    ) -> Path:
        """调用MinerU /file_parse接口，安全解压ZIP并返回content_list。"""
        endpoint = f"{self.api_url}/file_parse"
        fields = {
            "backend": backend or self.backend, "parse_method": parse_method, "lang_list": "ch",
            "effort": effort or self.effort,
            "table_enable": "true", "return_content_list": "true", "return_images": "true",
            "response_format_zip": "true",
        }
        if start_page_id is not None:
            fields["start_page_id"] = str(start_page_id)
        if end_page_id is not None:
            fields["end_page_id"] = str(end_page_id)
        mime = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
        # MinerU 是本地服务，不要让工作站代理变量把 IPv6 回环请求转发到网关。
        with source.open("rb") as handle, httpx.Client(timeout=self.timeout_seconds, trust_env=False) as client:
            multipart = [(name, (None, value)) for name, value in fields.items()]
            multipart.append(("files", (source.name, handle, mime)))
            response = client.post(endpoint, files=multipart)
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
                "heading_level": level if block_type == "heading" and text else None,
                "source": {
                    "image_ref": item.get("img_path") or item.get("image_path"),
                    "table_html": item.get("table_body") or item.get("html"),
                    "ocr_confidence": item.get("score", item.get("confidence")),
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


def resolve_image_refs(document: dict[str, Any], content_root: Path, extract_root: Path) -> None:
    """解析 MinerU 的相对图片路径，使可选 OCR 能够安全读取图片。"""
    safe_root = extract_root.resolve()
    for block in document.get("blocks", []):
        source = block.setdefault("source", {})
        raw = str(source.get("image_ref") or "")
        if not raw:
            continue
        path = Path(raw)
        candidate = path.resolve() if path.is_absolute() else (content_root / path).resolve()
        try:
            candidate.relative_to(safe_root)
        except ValueError:
            continue
        if not candidate.is_file():
            matches = list(extract_root.rglob(path.name))
            candidate = matches[0].resolve() if matches else candidate
        if candidate.is_file():
            source["image_ref"] = str(candidate)


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
