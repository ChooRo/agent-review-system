import json
from pathlib import Path

from app.core.config import get_settings
from app.review_engine.services.procurement.workflow import WorkflowEngine


class Store:
    def event(self, *_args, **_kwargs):
        pass


def test_workflow_retrieves_all_effective_legal_documents_when_gate_is_disabled(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "knowledge"
    for status in ("effective", "effective_unconfirmed", "unknown", "repealed"):
        directory = root / status; directory.mkdir(parents=True)
        effective = status == "effective"
        (directory / "legal_knowledge.json").write_text(json.dumps({
            "legal_document": {"document_key": status, "title": status, "status": "effective" if status.startswith("effective") else status, "metadata_version": 3, "applicability": {"activities": [{"value": "政府采购", "evidence": []}]}},
            "units": [{"legal_unit_id": status, "text": "procurement evidence", "search_text": "procurement evidence"}],
            "quality": {"status": "reviewable"},
            "metadata_extraction": {"status": "confirmed" if effective else "ready"},
        }), encoding="utf-8")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data")); get_settings.cache_clear()
    skills = Path(__file__).parents[1] / "app" / "review_engine" / "skills.json"
    engine = WorkflowEngine(tmp_path / "runs", skills, {"rules": {"knowledge_root": str(root)}})
    engine._previous = lambda _store, step: {} if step == "build_scene_view" else {"ledgers": {"procurement": [{"statement": "procurement evidence"}]}}
    result = engine._match_rules(Store(), {"scenario": "procurement"}, None, None)
    assert [item["source"]["document_key"] for item in result["legal_documents"]] == ["effective", "effective_unconfirmed"]
    assert result["legal_source_stats"] == {"included": 2, "excluded_unknown": 1, "excluded_repealed": 1, "excluded_unconfirmed_profile": 0, "excluded_other": 0}
    assert all("path" not in source for source in result["legal_sources"])
    get_settings.cache_clear()
