from fastapi.testclient import TestClient
from time import sleep

from app.core.config import get_settings
from app.main import app
from app.services.procurement import review as review_service


def login(client: TestClient, username: str) -> str:
    response = client.post("/api/v1/auth/login", json={"username": username, "password": "ChangeMe123!"})
    assert response.status_code == 200
    return response.json()["access_token"]


def wait_for_terminal(client: TestClient, task_url: str, headers: dict) -> dict:
    for _ in range(50):
        task = client.get(task_url, headers=headers).json()
        if task["status"] in {"operator_review", "failed"}:
            return task
        sleep(0.02)
    raise AssertionError("审查任务未在测试时限内结束")


def fake_completed_review(project_id, task_id, _doc_path, store_results, _fail_task, _progress_task, _engine_run_id=None, _task_context=None, *_gate_args) -> None:
    store_results(project_id, task_id, {"engine_run_id": "run-test", "quality": {"status": "passed"}, "task_legal_facts": {"project_type": "unknown"}, "legal_applicability": [], "legal_context_freeze": [], "findings": [{"risk_level": "medium", "title": "Test finding", "description": "Review candidate", "recommendation": "Confirm manually", "evidence": []}]})


def test_operator_can_create_one_procurement_task_per_project(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path)); monkeypatch.setenv("UPLOADS_DIR", str(tmp_path / "uploads")); get_settings.cache_clear()
    client = TestClient(app); headers = {"Authorization": f"Bearer {login(client, 'operator')}"}
    project = client.post("/api/v1/projects", headers=headers, json={"name": "office", "project_code": "PO-2026-01", "handling_department": "procurement", "project_owner": "operator"})
    assert project.status_code == 200
    task_url = f"/api/v1/projects/{project.json()['id']}/procurement-review-tasks"
    assert client.post(task_url, headers=headers, json={"title": "review"}).status_code == 200
    assert client.post(task_url, headers=headers, json={"title": "duplicate"}).status_code == 409
    get_settings.cache_clear()


def test_start_persists_review_findings_and_events(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path)); monkeypatch.setenv("UPLOADS_DIR", str(tmp_path / "uploads")); get_settings.cache_clear()
    monkeypatch.setattr(review_service, "run_review_workflow", fake_completed_review)
    client = TestClient(app); headers = {"Authorization": f"Bearer {login(client, 'operator')}"}
    project = client.post("/api/v1/projects", headers=headers, json={"name": "start", "project_code": "PO-START", "handling_department": "procurement", "project_owner": "operator"}).json()
    task = client.post(f"/api/v1/projects/{project['id']}/procurement-review-tasks", headers=headers, json={"title": "review"}).json()
    upload = client.post(f"/api/v1/projects/{project['id']}/procurement-review-tasks/{task['id']}/document", headers=headers, files={"file": ("test.pdf", b"%PDF-1.4\nterms", "application/pdf")})
    assert upload.status_code == 200
    task_url = f"/api/v1/projects/{project['id']}/procurement-review-tasks/{task['id']}"
    started = client.post(f"{task_url}/start", headers=headers)
    assert started.status_code == 200
    terminal = wait_for_terminal(client, task_url, headers)
    assert terminal["status"] == "operator_review" and "execution_mode" not in terminal
    assert terminal["legal_facts"] == {"project_type": "unknown"}
    findings = client.get(f"/api/v1/projects/{project['id']}/procurement-review-tasks/{task['id']}/findings", headers=headers).json()
    events = client.get(f"/api/v1/projects/{project['id']}/procurement-review-tasks/{task['id']}/events", headers=headers).json()
    assert all(finding["source"] for finding in findings) and events
    assert client.get(f"/api/v1/projects/{project['id']}/procurement-review-tasks/{task['id']}/events?after={events[0]['id']}", headers=headers).json() == events[1:]
    get_settings.cache_clear()


def test_procurement_review_end_to_end_recheck_flow(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path)); monkeypatch.setenv("UPLOADS_DIR", str(tmp_path / "uploads")); get_settings.cache_clear()
    monkeypatch.setattr(review_service, "run_review_workflow", fake_completed_review)
    client = TestClient(app)
    operator = {"Authorization": f"Bearer {login(client, 'operator')}"}
    primary = {"Authorization": f"Bearer {login(client, 'supervisor')}"}
    collaborator = {"Authorization": f"Bearer {login(client, 'legal_supervisor')}"}

    project = client.post("/api/v1/projects", headers=operator, json={"name": "e2e", "project_code": "PO-E2E", "handling_department": "procurement", "project_owner": "operator"}).json()
    task = client.post(f"/api/v1/projects/{project['id']}/procurement-review-tasks", headers=operator, json={"title": "review", "collaborative_supervisor_ids": [4]}).json()
    uploaded = client.post(f"/api/v1/projects/{project['id']}/procurement-review-tasks/{task['id']}/document", headers=operator, files={"file": ("procurement.pdf", b"%PDF-1.4\nprocurement terms", "application/pdf")}).json()
    assert uploaded["document"]["file_name"] == "procurement.pdf"
    task_url = f"/api/v1/projects/{project['id']}/procurement-review-tasks/{task['id']}"
    assert client.post(f"{task_url}/start", headers=operator).status_code == 200
    started = wait_for_terminal(client, task_url, operator)
    assert started["progress"] == 100 and started["task_role"] == "operator"
    findings_url = f"/api/v1/projects/{project['id']}/procurement-review-tasks/{task['id']}/findings"
    findings = client.get(findings_url, headers=operator).json()
    events_url = f"/api/v1/projects/{project['id']}/procurement-review-tasks/{task['id']}/events"
    events = client.get(events_url, headers=operator).json(); assert events
    for finding in findings:
        response = client.put(f"{findings_url}/{finding['id']}/operator-disposition", headers=operator, json={"action": "accept", "version": finding["version"]})
        assert response.status_code == 200
    assert client.post(f"/api/v1/projects/{project['id']}/procurement-review-tasks/{task['id']}/operator-submit", headers=operator).status_code == 200
    findings = client.get(findings_url, headers=primary).json()
    for finding in findings:
        response = client.put(f"{findings_url}/{finding['id']}/primary-decision", headers=primary, json={"decision": "receive", "version": finding["version"]})
        assert response.status_code == 200
    if not findings:
        assert started["finding_summary"]["total"] == 0
        return
    affected = client.get(findings_url, headers=collaborator).json()[0]
    comment = client.post(f"{findings_url}/{affected['id']}/collaborative-comments", headers=collaborator, json={"comment": "legal review"})
    assert comment.status_code == 200 and comment.json()["version"] == 1
    recheck = client.get(task_url, headers=primary).json()
    affected = client.get(findings_url, headers=primary).json()[0]
    assert recheck["status"] == "primary_recheck" and affected["recheck_required"] and affected["collaborative_comments"]
    response = client.put(f"{findings_url}/{affected['id']}/primary-decision", headers=primary, json={"decision": "receive", "version": affected["version"]})
    assert response.status_code == 200 and response.json()["primary_decision"]
    assert client.post(f"/api/v1/projects/{project['id']}/procurement-review-tasks/{task['id']}/primary-confirm", headers=primary).status_code == 200
    completed = client.get(task_url, headers=primary).json()
    assert completed["status"] == "completed"
    all_events = client.get(events_url, headers=operator).json()
    assert client.get(f"{events_url}?after={all_events[1]['id']}", headers=operator).json() == all_events[2:]
    get_settings.cache_clear()


def test_rectification_version_and_final_lock(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path)); monkeypatch.setenv("UPLOADS_DIR", str(tmp_path / "uploads")); get_settings.cache_clear()
    client = TestClient(app)
    operator = {"Authorization": f"Bearer {login(client, 'operator')}"}
    primary = {"Authorization": f"Bearer {login(client, 'supervisor')}"}
    project = client.post("/api/v1/projects", headers=operator, json={"name": "rectify", "project_code": "PO-RECTIFY", "handling_department": "procurement", "project_owner": "operator"}).json()
    task = client.post(f"/api/v1/projects/{project['id']}/procurement-review-tasks", headers=operator, json={"title": "review"}).json()
    task_url = f"/api/v1/projects/{project['id']}/procurement-review-tasks/{task['id']}"
    service = review_service.ProcurementReviewService()
    task_row, tasks = service._task(project["id"], task["id"])
    original = {"id": "doc-1", "file_name": "v1.pdf", "content_type": "application/pdf", "size": 10, "sha256": "v1", "path": str(tmp_path / "v1.pdf"), "version": 1, "uploaded_by": 1, "uploaded_at": "now"}
    task_row.update({"status": "completed", "document": original, "document_versions": [original]}); service.tasks.write(tasks)
    uploaded = client.post(f"{task_url}/rectification-document", headers=operator, files={"file": ("v2.pdf", b"%PDF-1.4\nrectified", "application/pdf")})
    assert uploaded.status_code == 200
    assert uploaded.json()["status"] == "rectification_draft" and uploaded.json()["document"]["version"] == 2
    task_row, tasks = service._task(project["id"], task["id"]); task_row["status"] = "completed"; service.tasks.write(tasks)
    locked = client.post(f"{task_url}/lock-final", headers=primary)
    assert locked.status_code == 200
    assert locked.json()["status"] == "final_locked" and locked.json()["final_baseline"]["document_version"] == 2
    get_settings.cache_clear()
