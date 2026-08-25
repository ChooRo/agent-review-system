from pathlib import Path

import pytest

from app.integrations.storage.local import LocalStorage


def test_local_storage_round_trip_and_path_guard(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    assert storage.upload("project/document.pdf", b"data") == "project/document.pdf"
    assert storage.read("project/document.pdf") == b"data"
    assert storage.download_url("project/document.pdf").startswith("file:")
    storage.delete("project/document.pdf")
    with pytest.raises(FileNotFoundError):
        storage.read("project/document.pdf")
    with pytest.raises(ValueError):
        storage.upload("../outside", b"no")
