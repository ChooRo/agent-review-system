from fastapi.testclient import TestClient

from app.main import app


def test_knowledge_api_requires_login_and_is_readable_by_operator() -> None:
    client = TestClient(app)
    assert client.get("/api/v1/knowledge").status_code == 401
    login = client.post("/api/v1/auth/login", json={"username": "operator", "password": "ChangeMe123!"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    documents = client.get("/api/v1/knowledge", headers=headers)
    assert documents.status_code == 200 and documents.json()
    assert "article_count" in documents.json()[0]
    rules = client.get("/api/v1/knowledge/rules", headers=headers)
    assert rules.status_code == 200
    assert rules.json() == []
    key = documents.json()[0]["document_key"]
    detail = client.get(f"/api/v1/knowledge/{key}", headers=headers)
    assert detail.status_code == 200
    assert all(unit.get("legal_unit_id") for unit in detail.json().get("units", []))
