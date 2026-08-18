from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import httpx

from app.review_engine.services.mineru import MinerUService


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

    monkeypatch.setattr("app.review_engine.services.mineru.httpx.Client", FakeClient)
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

    monkeypatch.setattr("app.review_engine.services.mineru.httpx.Client", FakeClient)
    image = tmp_path / "page.png"
    image.write_bytes(b"png")
    document = {"blocks": [{"block_type": "image", "text": "", "source": {"image_ref": str(image)}}]}
    service = MinerUService(ocr_config={"api_url": "http://127.0.0.1:8002/v1", "model": "deepseek-ai/DeepSeek-OCR"})
    service._supplement_images(document)
    assert document["blocks"][0]["text"] == "图中要求供应商提供营业执照"
    assert document["blocks"][0]["source"]["ocr_raw_text"].startswith("OCR this image.")
    assert document["blocks"][0]["source"]["ocr_provider"] == "deepseek_ocr"
    assert captured["messages"][0]["content"][0]["type"] == "image_url"
