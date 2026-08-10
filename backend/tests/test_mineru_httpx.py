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
            return httpx.Response(
                200,
                content=archive.getvalue(),
                request=httpx.Request("POST", "http://[::1]:8000/file_parse"),
            )

    monkeypatch.setattr("app.review_engine.services.mineru.httpx.Client", FakeClient)
    source = tmp_path / "input.pdf"
    source.write_bytes(b"%PDF-1.4")

    MinerUService(api_url="http://[::1]:8000")._call_api(source, tmp_path / "output")

    assert captured["trust_env"] is False
