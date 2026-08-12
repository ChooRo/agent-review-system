import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app
from app.repositories.knowledge_repository import KnowledgeRepository
from app.services import knowledge as knowledge_service_module
from app.services.knowledge import KnowledgeService


def login(client: TestClient, username: str) -> tuple[dict[str, str], dict]:
    response = client.post("/api/v1/auth/login", json={"username": username, "password": "ChangeMe123!"})
    assert response.status_code == 200
    body = response.json()
    return {"Authorization": f"Bearer {body['access_token']}"}, body["user"]


def fake_ingest(source: Path, output_dir: Path, mineru, metadata: dict) -> dict:
    assert mineru.api_url == "http://127.0.0.1:8001"
    output_dir.mkdir(parents=True)
    knowledge = {
        "legal_document": {"document_key": "law_test", "title": metadata.get("title") or "Untitled", "issuer": metadata.get("issuer"), "effective_date": metadata.get("effective_date"), "status": "effective"},
        "units": [{"legal_unit_id": "LAW-law_test-A0001-P01", "article_index": 1, "article_no": "第一条", "text": "采购活动适用本法。", "search_text": "采购活动适用本法。", "status": "unknown", "effective_date": None}],
        "quality": {"status": "needs_metadata", "article_count": 1},
    }
    (output_dir / "document.json").write_text(json.dumps({"blocks": []}), encoding="utf-8")
    (output_dir / "legal_knowledge.json").write_text(json.dumps(knowledge), encoding="utf-8")
    return knowledge


class FakeMetadataLLM:
    def __init__(self, _config, _store):
        pass

    def json_call(self, _step, _prompt, payload):
        source = payload["units"][0]
        return {"applicability": {"activities": [{"value": "采购活动", "evidence": [{"legal_unit_id": source["legal_unit_id"], "quote": source["text"]}]}]}}


def setup_upload_service(tmp_path: Path, monkeypatch) -> tuple[TestClient, Path, Path]:
    data_dir, knowledge_root = tmp_path / "data", tmp_path / "knowledge" / "rules"
    monkeypatch.setenv("DATA_DIR", str(data_dir)); monkeypatch.setenv("MINERU_API_URL", "http://127.0.0.1:8001"); get_settings.cache_clear()
    service = KnowledgeService(); service.repository = KnowledgeRepository(knowledge_root, data_dir)
    app.dependency_overrides[KnowledgeService] = lambda: service
    monkeypatch.setattr(knowledge_service_module, "ingest_legal_document", fake_ingest)
    return TestClient(app), data_dir, knowledge_root


def upload(client: TestClient, headers: dict[str, str], department: str, **extra) -> dict:
    response = client.post(
        "/api/v1/knowledge/documents", headers=headers,
        data={"title": "Test law", "issuer": "Issuer", "department": department, "document_version": "2026.1", "applicable_scope": "procurement", **extra},
        files={"file": ("law.pdf", b"%PDF-1.4\nbody", "application/pdf")},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_document_metadata_permissions_visibility_and_optimistic_lock(tmp_path, monkeypatch) -> None:
    client, _data_dir, root = setup_upload_service(tmp_path, monkeypatch)
    try:
        admin, _ = login(client, "admin"); operator, _ = login(client, "operator")
        supervisor, supervisor_user = login(client, "supervisor"); other, _ = login(client, "legal_supervisor")
        item = upload(client, admin, supervisor_user["department"], status="effective")
        assert item["status"] == "unknown" and item["document_version"] == "2026.1" and item["metadata_version"] == 1
        assert (root / "law_test" / "original.pdf").is_file() and (root / "law_test" / "legal_knowledge.json").is_file()
        assert client.get("/api/v1/knowledge", headers=operator).json() == []
        assert client.get("/api/v1/knowledge", headers=admin).json()[0]["status"] == "unknown"
        assert client.get("/api/v1/knowledge", headers=supervisor).json() == []
        assert client.get("/api/v1/knowledge/law_test", headers=operator).status_code == 404
        assert client.patch("/api/v1/knowledge/documents/law_test", headers=supervisor, json={"metadata_version": 1, "status": "effective"}).status_code == 403
        assert client.patch("/api/v1/knowledge/documents/law_test", headers=other, json={"metadata_version": 1, "applicable_scope": "x"}).status_code == 403
        stored_path = root / "law_test" / "legal_knowledge.json"
        stored_value = json.loads(stored_path.read_text(encoding="utf-8")); stored_value["metadata_extraction"]["status"] = "ready"
        stored_path.write_text(json.dumps(stored_value), encoding="utf-8")
        effective = client.patch("/api/v1/knowledge/documents/law_test", headers=admin, json={"metadata_version": 1, "status": "effective", "effective_date": "2026-01-01", "applicable_scope": "procurement"})
        assert effective.status_code == 200 and effective.json()["status"] == "effective" and effective.json()["metadata_version"] == 2
        assert client.get("/api/v1/knowledge", headers=operator).json()[0]["document_key"] == "law_test"
        assert client.get("/api/v1/knowledge", headers=supervisor).json()[0]["document_key"] == "law_test"
        assert client.patch("/api/v1/knowledge/documents/law_test", headers=admin, json={"metadata_version": 1, "applicable_scope": "x"}).status_code == 409
        repealed = client.patch("/api/v1/knowledge/documents/law_test", headers=admin, json={"metadata_version": 2, "status": "repealed"})
        assert repealed.status_code == 200 and repealed.json()["status"] == "repealed"
        assert client.get("/api/v1/knowledge", headers=operator).json() == []
        stored = json.loads((root / "law_test" / "legal_knowledge.json").read_text(encoding="utf-8"))
        assert len(stored["metadata_history"]) == 2 and stored["legal_document"]["metadata_version"] == 3
        assert client.get("/api/v1/knowledge/rules", headers=operator).json() == []
    finally:
        app.dependency_overrides.clear(); get_settings.cache_clear()


def test_knowledge_upload_rejects_non_admin_invalid_duplicate_and_parse_failure(tmp_path, monkeypatch) -> None:
    client, data_dir, root = setup_upload_service(tmp_path, monkeypatch)
    try:
        admin, admin_user = login(client, "admin"); operator, _ = login(client, "operator"); supervisor, _ = login(client, "supervisor")
        file = {"file": ("law.pdf", b"%PDF-1.4", "application/pdf")}
        assert client.post("/api/v1/knowledge/documents", headers=operator, files=file).status_code == 403
        assert client.post("/api/v1/knowledge/documents", headers=supervisor, files=file).status_code == 403
        assert client.post("/api/v1/knowledge/documents", headers=admin, files={"file": ("law.txt", b"text", "text/plain")}).status_code == 400
        upload(client, admin, admin_user["department"])
        assert client.post("/api/v1/knowledge/documents", headers=admin, files=file).status_code == 409
        monkeypatch.setattr(knowledge_service_module, "ingest_legal_document", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("parser unavailable")))
        assert client.post("/api/v1/knowledge/documents", headers=admin, data={"title": "Other law"}, files=file).status_code == 502
        assert sorted(path.name for path in root.iterdir()) == ["law_test"]
        temporary = data_dir / "knowledge_ingest"
        assert not temporary.exists() or not any(temporary.iterdir())
    finally:
        app.dependency_overrides.clear(); get_settings.cache_clear()


def test_metadata_extraction_requires_admin_and_confirmation_propagates_units(tmp_path, monkeypatch) -> None:
    client, _data_dir, root = setup_upload_service(tmp_path, monkeypatch)
    try:
        admin, admin_user = login(client, "admin"); supervisor, _ = login(client, "supervisor")
        item = upload(client, admin, admin_user["department"], effective_date="2026-01-01")
        assert item["extraction_status"] == "pending_ai"
        assert client.patch("/api/v1/knowledge/documents/law_test", headers=admin, json={"metadata_version": 1, "status": "effective"}).status_code == 409
        assert client.post("/api/v1/knowledge/documents/law_test/extract-metadata", headers=supervisor).status_code == 403
        stored_path = root / "law_test" / "legal_knowledge.json"
        stored_value = json.loads(stored_path.read_text(encoding="utf-8")); stored_value["metadata_extraction"]["status"] = "failed"
        stored_path.write_text(json.dumps(stored_value), encoding="utf-8")
        assert client.patch("/api/v1/knowledge/documents/law_test", headers=admin, json={"metadata_version": 1, "status": "effective"}).status_code == 409
        monkeypatch.setattr(knowledge_service_module, "load_review_settings", lambda _path: {"llm": {"api_url": "https://example.invalid/v1", "api_key": "key", "model": "model"}})
        monkeypatch.setattr(knowledge_service_module, "LLMService", FakeMetadataLLM)
        extracted = client.post("/api/v1/knowledge/documents/law_test/extract-metadata", headers=admin)
        assert extracted.status_code == 200
        assert extracted.json()["metadata_extraction"]["status"] == "ready"
        assert extracted.json()["metadata_extraction"]["applicability"]["activities"][0]["evidence"][0]["legal_unit_id"] == "LAW-law_test-A0001-P01"
        confirmed = client.patch("/api/v1/knowledge/documents/law_test", headers=admin, json={"metadata_version": 2, "status": "effective"})
        assert confirmed.status_code == 200 and confirmed.json()["extraction_status"] == "confirmed"
        stored = json.loads((root / "law_test" / "legal_knowledge.json").read_text(encoding="utf-8"))
        assert stored["legal_document"]["applicability"]["activities"][0]["value"] == "采购活动"
        assert stored["units"][0]["status"] == "effective"
        assert stored["units"][0]["effective_date"] == "2026-01-01"
    finally:
        app.dependency_overrides.clear(); get_settings.cache_clear()
