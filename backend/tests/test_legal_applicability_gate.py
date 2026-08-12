from pathlib import Path

from app.review_engine.services.workflow import WorkflowEngine, derive_task_legal_facts, match_legal_documents


def source(key: str) -> dict:
    return {
        "document_key": key,
        "title": key,
        "source_freeze": {
            "document_key": key,
            "metadata_version": 2,
            "source_fingerprint": "sha256:source",
            "content_fingerprint": "sha256:content",
            "fallbacks": [],
        },
    }


def legal_document(key: str, profile: dict) -> dict:
    return {
        "source": source(key),
        "applicability": profile,
        "units": [{"legal_unit_id": f"{key}-unit", "text": "政府采购活动", "search_text": "政府采购活动"}],
    }


PROFILE = {"activities": [{"value": "政府采购活动", "evidence": [{"legal_unit_id": "L-1", "quote": "政府采购活动"}]}]}


def facts(value: str) -> dict:
    return {
        "project_type": "unknown", "procurement_method": "unknown", "is_government_procurement": value,
        "is_engineering_related": "unknown", "is_mandatory_tender": "unknown", "region": "unknown",
        "review_stage": "procurement_document_review", "evidence": {"is_government_procurement": [{"block_id": "B-1", "quote": "政府采购"}]},
    }


def test_task_legal_facts_are_explicit_or_unknown() -> None:
    result = derive_task_legal_facts(
        {"procurement": {"blocks": [{"block_id": "B-1", "text": "本项目为政府采购公开招标项目。"}]}}, [], {}
    )
    assert result["is_government_procurement"] == "yes"
    assert result["procurement_method"] == "open_tender"
    assert result["project_type"] == "unknown"
    assert result["evidence"]["is_government_procurement"][0]["block_id"] == "B-1"


def test_legal_applicability_statuses_and_frozen_source_are_traceable() -> None:
    document = legal_document("effective-confirmed", PROFILE)
    assert match_legal_documents(facts("yes"), [document])[0]["status"] == "applicable"
    assert match_legal_documents(facts("no"), [document])[0]["status"] == "not_applicable"
    unknown = match_legal_documents(facts("unknown"), [document])[0]
    assert unknown["status"] == "insufficient_facts"
    assert unknown["missing_facts"] == ["is_government_procurement"]
    assert unknown["source_freeze"]["metadata_version"] == 2
    potential = match_legal_documents(facts("yes"), [legal_document("subject-only", {"subjects": [{"value": "采购人", "evidence": []}]})])[0]
    assert potential["status"] == "potential"


def test_formal_legal_context_uses_only_applicable_documents(tmp_path: Path) -> None:
    skills = Path(__file__).parents[1] / "app" / "review_engine" / "skills.json"
    engine = WorkflowEngine(tmp_path / "runs", skills)
    applicable = legal_document("applicable", PROFILE)
    insufficient = legal_document("insufficient", {"trigger_conditions": [{"value": "依法必须招标项目", "evidence": []}]})
    previous = {
        "derive_legal_facts": {"task_legal_facts": facts("yes")},
        "match_rules": {"legal_documents": [applicable, insufficient], "warnings": []},
        "build_ledger": {"ledgers": {"procurement": [{"statement": "政府采购活动"}]}},
    }
    engine._previous = lambda _store, step: previous[step]
    result = engine._match_legal_applicability(None, {}, None, None)
    assert [unit["legal_unit_id"] for unit in result["applicable_legal_units"]] == ["applicable-unit"]
    assert result["frozen_context"] == [source("applicable")["source_freeze"]]
    assert {item["document_key"]: item["status"] for item in result["decisions"]} == {"applicable": "applicable", "insufficient": "insufficient_facts"}


def test_agent_review_receives_only_applicable_legal_units(tmp_path: Path) -> None:
    skills = Path(__file__).parents[1] / "app" / "review_engine" / "skills.json"
    engine = WorkflowEngine(tmp_path / "runs", skills)
    captured = {}

    class LLM:
        def json_call(self, _step, _prompt, payload):
            captured.update(payload)
            return {"overall_conclusion": "manual confirmation", "findings": []}

    previous = {
        "build_scene_view": {"topic_views": {}}, "quality_check": {"quality": {}}, "global_validation": {},
        "match_rules": {"rules": [], "matched_count": 0, "rule_source": "test", "warnings": [], "legal_documents": [legal_document("potential", PROFILE)]},
        "match_legal_applicability": {"applicable_legal_units": [{"legal_unit_id": "applicable-unit"}], "decisions": [{"document_key": "potential", "status": "potential"}]},
    }
    engine._previous = lambda _store, step: previous[step]
    engine._agent_review(None, {"scenario": "procurement"}, LLM(), None)
    assert "legal_documents" not in captured["matched_rules"]
    assert captured["legal_context"]["applicable_legal_units"] == [{"legal_unit_id": "applicable-unit"}]
