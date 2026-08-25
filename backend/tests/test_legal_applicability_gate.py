from pathlib import Path

from app.review_engine.legal.metadata import derive_task_legal_facts, match_legal_documents
from app.review_engine.procurement.agent_workflow import WorkflowEngine


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
    engine = WorkflowEngine(tmp_path / "runs", skills, {"legal_applicability": {"enabled": True}})
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


def test_disabled_gate_treats_all_eligible_laws_as_applicable(tmp_path: Path) -> None:
    skills = Path(__file__).parents[1] / "app" / "review_engine" / "skills.json"
    engine = WorkflowEngine(tmp_path / "runs", skills, {"legal_applicability": {"enabled": False}})
    documents = [legal_document("first", PROFILE), legal_document("second", {})]
    previous = {
        "match_rules": {"legal_documents": documents, "warnings": []},
        "build_ledger": {"ledgers": {"procurement": []}},
    }
    engine._previous = lambda _store, step: previous[step]
    result = engine._match_legal_applicability(None, {}, None, None)
    assert result["mode"] == "all_eligible_laws"
    assert result["execution_status"] == "degraded"
    assert result["degraded_reasons"] == ["legal_applicability_disabled"]
    assert {item["document_key"] for item in result["decisions"]} == {"first", "second"}
    assert all(item["status"] == "applicable" for item in result["decisions"])


def test_agent_review_only_merges_existing_evidence_candidates(tmp_path: Path) -> None:
    skills = Path(__file__).parents[1] / "app" / "review_engine" / "skills.json"
    engine = WorkflowEngine(tmp_path / "runs", skills)
    captured = {}

    class LLM:
        def json_call(self, _step, prompt, payload):
            captured["prompt"] = prompt
            captured.update(payload)
            return {
                "overall_conclusion": "发现一项采购文件问题。",
                "findings": [
                    {
                        "title": "模型凭空新增的问题",
                        "source_candidate_ids": [],
                    },
                    {
                        "title": "采购文件缺少法定内容",
                        "description": "已提供法规要求列明该内容",
                        "source_candidate_ids": ["CND-1"],
                        "evidence_block_ids": ["B-1", "invented-block"],
                        "legal_unit_ids": ["applicable-unit"],
                    },
                ],
            }

    previous = {
        "match_rules": {"rules": [], "matched_count": 0, "rule_source": "test", "warnings": [], "legal_documents": [legal_document("potential", PROFILE)]},
        "match_legal_applicability": {"mode": "applicability_gate", "decisions": [{"document_key": "potential", "status": "applicable"}]},
        "build_compliance_matrix": {
            "coverage_matrix": [{"topic": "资格与实质性条件", "coverage_status": "reviewed", "fact_count": 1, "legal_unit_count": 1}],
            "candidate_findings": [{
                "candidate_id": "CND-1", "title": "采购文件缺少法定内容", "finding_type": "legal_risk",
                "evidence_block_ids": ["B-1"], "legal_unit_ids": ["applicable-unit"], "rule_ids": [],
            }],
        },
    }
    engine._previous = lambda _store, step: previous[step]
    result = engine._agent_review(None, {"scenario": "procurement"}, LLM(), None)
    assert "只能去重、合并review_candidates中已有候选" in captured["prompt"]
    assert captured["legal_context"]["mode"] == "applicability_gate"
    assert "scene_view" not in captured and "applicable_legal_units" not in captured["legal_context"]
    assert [item["title"] for item in result["findings"]] == ["采购文件缺少法定内容"]
    assert result["findings"][0]["evidence_block_ids"] == ["B-1"]
    assert result["findings"][0]["legal_applicability"] == "applicable"
