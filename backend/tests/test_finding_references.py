from app.core.config import get_settings
from app.services.procurement.review import ProcurementReviewService


def test_legal_evidence_is_not_persisted_as_executable_rule(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path)); get_settings.cache_clear()
    service = ProcurementReviewService()
    service.repository.commit({
        "projects": [{"id": "project_1", "name": "project", "project_code": "P-1", "handling_department": "procurement",
                      "project_owner": "经办", "status": "draft", "created_by": 1,
                      "created_at": "2026-01-01T00:00:00+00:00", "updated_at": "2026-01-01T00:00:00+00:00",
                      "version": 1}],
        "tasks": [{"id": "task_1", "project_id": "project_1", "title": "review", "status": "reviewing",
                   "created_at": "2026-01-01T00:00:00+00:00", "updated_at": "2026-01-01T00:00:00+00:00",
                   "progress": 0, "version": 1}],
        "findings": [], "comments": [], "events": [], "audit": [], "idempotency": [],
    })
    service._store_review_results("project_1", "task_1", {"findings": [{"legal_evidence": [{"legal_unit_id": "law:1", "document_title": "招标法", "article_no": "第一条", "text": "法规原文"}], "rule_ids": [], "description": "候选问题"}]})
    finding = service.findings.read()["items"][0]
    assert finding["rule_refs"] == []
    assert finding["legal_refs"][0]["legal_unit_id"] == "law:1"
    get_settings.cache_clear()
