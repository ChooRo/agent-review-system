from pathlib import Path

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app
from app.repositories.rule_repository import RuleRepository


def login(client: TestClient, username: str) -> tuple[dict[str, str], dict]:
    response = client.post("/api/v1/auth/login", json={"username": username, "password": "ChangeMe123!"})
    assert response.status_code == 200
    body = response.json()
    return {"Authorization": f"Bearer {body['access_token']}"}, body["user"]


def test_rule_lifecycle_permissions_versions_and_knowledge_compatibility(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path)); get_settings.cache_clear()
    client = TestClient(app)
    admin, _ = login(client, "admin")
    operator, _ = login(client, "operator")
    owner, owner_user = login(client, "supervisor")
    other_supervisor, _ = login(client, "legal_supervisor")
    payload = {
        "title": "Temporary test rule", "description": "Test-only structured asset", "decision_criteria": "A document must contain the required evidence.",
        "risk_level": "mandatory", "module": "procurement", "department": owner_user["department"], "tags": ["evidence"], "source_type": "manual",
        "legal_document_key": "legal-test", "legal_unit_ids": ["unit-test"],
    }
    assert client.post("/api/v1/rules", headers=owner, json=payload).status_code == 403
    created = client.post("/api/v1/rules", headers=admin, json=payload)
    assert created.status_code == 200 and created.json()["status"] == "pending_confirmation"
    rule = created.json(); rule_id = rule["id"]
    assert client.get("/api/v1/rules", headers=operator).json() == []
    assert client.post(f"/api/v1/rules/{rule_id}/confirm", headers=other_supervisor, json={"version": 1}).status_code == 403
    assert client.patch(f"/api/v1/rules/{rule_id}", headers=owner, json={"title": "not allowed", "version": 1}).status_code == 403
    confirmed = client.post(f"/api/v1/rules/{rule_id}/confirm", headers=admin, json={"version": 1})
    assert confirmed.status_code == 200 and confirmed.json()["status"] == "published"
    assert client.get("/api/v1/rules", headers=owner).json()[0]["id"] == rule_id
    assert client.get("/api/v1/knowledge/rules", headers=operator).json()[0]["id"] == rule_id

    revised = client.patch(f"/api/v1/rules/{rule_id}", headers=admin, json={"title": "Temporary revised rule", "version": 1})
    assert revised.status_code == 200 and revised.json()["status"] == "pending_confirmation" and revised.json()["version"] == 2
    operator_rule = client.get(f"/api/v1/rules/{rule_id}", headers=operator).json()
    assert operator_rule["title"] == "Temporary test rule" and operator_rule["status"] == "published"
    versions = client.get(f"/api/v1/rules/{rule_id}/versions", headers=admin).json()
    assert versions[0]["title"] == "Temporary test rule" and len(versions) >= 3
    operator_versions = client.get(f"/api/v1/rules/{rule_id}/versions", headers=operator).json()
    assert operator_versions and all(item["status"] == "published" for item in operator_versions)
    assert all(item["title"] != "Temporary revised rule" for item in operator_versions)
    assert any(item["title"] == "Temporary revised rule" for item in versions)
    assert any(item["title"] == "Temporary revised rule" for item in client.get(f"/api/v1/rules/{rule_id}/versions", headers=admin).json())
    assert client.get("/api/v1/knowledge/rules", headers=operator).json()[0]["title"] == "Temporary test rule"
    assert RuleRepository(tmp_path).applicable_rules("procurement")[0]["title"] == "Temporary test rule"
    assert client.patch(f"/api/v1/rules/{rule_id}", headers=admin, json={"title": "stale", "version": 1}).status_code == 409
    assert client.post(f"/api/v1/rules/{rule_id}/confirm", headers=admin, json={"version": 2}).json()["status"] == "published"

    assert client.post(f"/api/v1/rules/{rule_id}/expire", headers=owner, json={"version": 2, "reason": "Test expiry"}).status_code == 403
    expired = client.post(f"/api/v1/rules/{rule_id}/expire", headers=admin, json={"version": 2, "reason": "Test expiry"})
    assert expired.status_code == 200 and expired.json()["status"] == "expired"
    assert client.get("/api/v1/rules", headers=operator).json() == []
    assert client.post(f"/api/v1/rules/{rule_id}/reactivate", headers=owner, json={"version": 3}).status_code == 403
    reactivated = client.post(f"/api/v1/rules/{rule_id}/reactivate", headers=admin, json={"version": 3})
    assert reactivated.status_code == 200 and reactivated.json()["status"] == "pending_confirmation" and reactivated.json()["version"] == 4
    assert client.post(f"/api/v1/rules/{rule_id}/confirm", headers=admin, json={"version": 4}).json()["status"] == "published"
    get_settings.cache_clear()


def test_rule_repository_never_exposes_candidate_to_engine(tmp_path: Path) -> None:
    repository = RuleRepository(tmp_path)
    candidate = {"id": "candidate", "module": "procurement", "status": "pending_confirmation"}
    repository.transaction(lambda state: state["rules"].append(candidate))
    assert repository.applicable_rules("procurement") == []
