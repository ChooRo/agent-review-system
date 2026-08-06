import json

import pytest

from app.repositories.json_store import JsonStore
from app.repositories.review_repository import ReviewRepository


def test_json_store_atomic_write_remains_parseable(tmp_path) -> None:
    path = tmp_path / "data.json"
    store = JsonStore(path)
    store.write({"schema_version": 1, "items": [{"id": "one", "version": 1}]})
    assert json.loads(path.read_text(encoding="utf-8"))["items"][0]["id"] == "one"


def test_repository_uses_one_business_file(tmp_path) -> None:
    repository = ReviewRepository(tmp_path)
    projects = repository.collection("projects")
    projects.write({"items": [{"id": "prj_1", "version": 1}]})
    assert (tmp_path / "review_data.json").is_file()
    assert repository.collection("projects").read()["items"][0]["id"] == "prj_1"


def test_corrupt_json_fails_explicitly(tmp_path) -> None:
    path = tmp_path / "bad.json"; path.write_text("{", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError): JsonStore(path).read()
