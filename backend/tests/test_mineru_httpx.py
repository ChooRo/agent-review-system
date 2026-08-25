from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import httpx

from app.integrations.mineru import MinerUService


def test_mineru_client_does_not_inherit_environment_proxies(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}
    archive = BytesIO()
    with ZipFile(archive, "w") as zip_file:
        zip_file.writestr("result_content_list.json", "[]")

    class FakeClient:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def post(self, *_args, **_kwargs) -> httpx.Response:
            captured["fields"] = dict((name, value[1]) for name, value in _kwargs["files"] if value[0] is None)
            return httpx.Response(
                200,
                content=archive.getvalue(),
                request=httpx.Request("POST", "http://[::1]:8000/file_parse"),
            )

    monkeypatch.setattr("app.integrations.mineru.httpx.Client", FakeClient)
    source = tmp_path / "input.pdf"
    source.write_bytes(b"%PDF-1.4")

    MinerUService(api_url="http://[::1]:8000")._call_api(
        source, tmp_path / "output", start_page_id=3, end_page_id=5,
    )

    assert captured["trust_env"] is False
    assert captured["fields"]["backend"] == "pipeline"
    assert captured["fields"]["effort"] == "medium"
    assert captured["fields"]["start_page_id"] == "3"
    assert captured["fields"]["end_page_id"] == "5"


def test_deepseek_ocr_supplements_empty_image_blocks(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    class FakeClient:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def post(self, *_args, **kwargs) -> httpx.Response:
            captured.update(kwargs["json"])
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "OCR this image.\n图中要求供应商提供营业执照"}}]},
                request=httpx.Request("POST", "http://127.0.0.1:8002/v1/chat/completions"),
            )

    monkeypatch.setattr("app.integrations.mineru.httpx.Client", FakeClient)
    image = tmp_path / "page.png"
    image.write_bytes(b"png")
    document = {"blocks": [{"block_type": "image", "text": "", "source": {"image_ref": str(image)}}]}
    service = MinerUService(ocr_config={"api_url": "http://127.0.0.1:8002/v1", "model": "deepseek-ai/DeepSeek-OCR"})
    service._supplement_images(document)
    assert document["blocks"][0]["text"] == "图中要求供应商提供营业执照"
    assert document["blocks"][0]["source"]["ocr_raw_text"].startswith("OCR this image.")
    assert document["blocks"][0]["source"]["ocr_provider"] == "deepseek_ocr"
    assert captured["messages"][0]["content"][0]["type"] == "image_url"


def test_deepseek_ocr_retries_server_error_and_records_final_http_detail(monkeypatch, tmp_path: Path) -> None:
    calls = 0

    class FakeClient:
        def __init__(self, **_kwargs) -> None: pass
        def __enter__(self): return self
        def __exit__(self, *_args) -> None: return None
        def post(self, *_args, **_kwargs) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(
                503, text="model temporarily unavailable",
                request=httpx.Request("POST", "http://127.0.0.1:8002/v1/chat/completions"),
            )

    monkeypatch.setattr("app.integrations.mineru.httpx.Client", FakeClient)
    image = tmp_path / "page.png"
    image.write_bytes(b"png")
    document = {"blocks": [{"block_type": "image", "text": "", "source": {"image_ref": str(image)}}]}
    MinerUService(ocr_config={"api_url": "http://127.0.0.1:8002/v1", "model": "ocr"})._supplement_images(document)
    source = document["blocks"][0]["source"]
    assert calls == 2
    assert source["ocr_status_code"] == 503
    assert source["ocr_error_detail"] == "model temporarily unavailable"


def test_native_pdf_text_supplements_only_pages_dropped_by_layout_parser(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "input.pdf"
    source.write_bytes(b"%PDF-1.4")
    document = {"blocks": [
        {"block_id": "B-1", "block_type": "paragraph", "page_no": 1, "text": "第一页已有正文"},
        {"block_id": "B-2", "block_type": "header", "page_no": 2, "text": "重复页眉"},
        {"block_id": "B-3", "block_type": "table", "page_no": 2, "text": ""},
    ]}
    service = MinerUService()
    fallback_text = "第二页续表文字，包含可供审查的完整评分标准和响应要求。" * 2
    monkeypatch.setattr(service, "_native_pdf_pages", lambda _source: ["第一页原文", fallback_text])

    service._supplement_missing_pdf_pages(document, source)

    fallback = document["blocks"][-1]
    assert fallback["block_id"] == "NATIVE-P0002"
    assert fallback["text"] == fallback_text
    assert fallback["source"]["parser_fallback"] == "native_pdf_text"


def test_native_pdf_text_can_be_attached_to_a_damaged_table_page(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "input.pdf"
    source.write_bytes(b"%PDF-1.4")
    text = "表格文字回退内容，包含完整的条款和评分标准。" * 3
    document = {"blocks": [{"block_id": "B-1", "block_type": "table", "page_no": 2, "text": "错误表格"}]}
    service = MinerUService()
    monkeypatch.setattr(service, "_native_pdf_pages", lambda _source: ["第一页", text])

    service.supplement_native_pdf_pages(document, source, {2}, reason="table_content_recovery")

    fallback = document["blocks"][-1]
    assert fallback["page_no"] == 2
    assert fallback["source"]["fallback_reason"] == "table_content_recovery"
