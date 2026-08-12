from app.review_engine.services.legal_metadata import (
    candidate_batches,
    extract_applicability,
    load_skill_instructions,
    prepare_metadata_extraction,
    select_candidate_units,
)


def unit(index: int, text: str) -> dict:
    return {
        "legal_unit_id": f"LAW-test-A{index:04d}-P01",
        "article_index": index,
        "article_no": f"第{index}条",
        "chapter": "总则",
        "text": text,
        "search_text": text,
    }


class FakeLLM:
    def json_call(self, _step, _prompt, payload):
        valid = payload["units"][0]
        return {"applicability": {
            "activities": [{"value": valid["text"], "evidence": [{"legal_unit_id": valid["legal_unit_id"], "quote": valid["text"]}]}],
            "subjects": [{"value": "虚构主体", "evidence": [{"legal_unit_id": "missing", "quote": "不存在"}]}],
        }}


def test_local_selection_batches_and_ai_evidence_validation() -> None:
    units = [unit(index, "一般规定") for index in range(1, 31)]
    units[9]["text"] = units[9]["search_text"] = "在中华人民共和国境内进行招标投标活动，适用本法。"
    selected = select_candidate_units(units)
    assert units[9] in selected and len(selected) < len(units)
    assert all(len(batch) <= 24 for batch in candidate_batches(selected))
    applicability, warnings = extract_applicability(FakeLLM(), selected)
    assert applicability["activities"][0]["evidence"][0]["legal_unit_id"] in {item["legal_unit_id"] for item in selected}
    assert applicability["subjects"] == []
    assert warnings[0]["code"] == "INVALID_AI_EVIDENCE"


def test_legal_applicability_extraction_uses_formal_skill() -> None:
    instructions = load_skill_instructions()
    assert "name: extract-legal-applicability-profile" in instructions
    assert "不得补充常识" in instructions
    assert "legal_unit_id" in instructions


def test_applicability_summary_uses_only_verified_fields() -> None:
    applicability, _ = extract_applicability(FakeLLM(), [unit(1, "招标投标活动适用本法。")])
    summary = applicability["summary"]
    assert "招标投标活动" in summary
    assert "虚构主体" not in summary
    assert len(summary) <= 160


def test_applicability_summary_is_two_to_three_lines_when_evidence_is_complete() -> None:
    value = "境内招标投标活动及采购项目的供应商"
    evidence = [{"legal_unit_id": "LAW-1", "quote": value}]
    applicability = {
        "activities": [{"value": "境内招标投标活动", "evidence": evidence}],
        "subjects": [{"value": "采购人和投标人", "evidence": evidence}],
        "business_phases": [{"value": "招标、投标和评标阶段", "evidence": evidence}],
        "trigger_conditions": [{"value": "依法必须招标的项目", "evidence": evidence}],
        "project_types": [],
        "exclusions": [{"value": "法律另有规定的除外", "evidence": evidence}],
        "precedence_rules": [],
    }
    from app.review_engine.services.legal_metadata import summarize_applicability

    summary = summarize_applicability(applicability)
    assert 80 <= len(summary) <= 160
    assert "采购人和投标人" in summary and "法律另有规定的除外" in summary


def test_selection_does_not_treat_every_mandatory_tender_clause_as_scope() -> None:
    units = [unit(index, "依法必须进行招标的项目应当遵守本条程序。") for index in range(1, 101)]
    units[49]["text"] = units[49]["search_text"] = "在中华人民共和国境内进行招标投标活动，适用本法。"
    selected = select_candidate_units(units)
    assert units[49] in selected
    assert len(selected) <= 24
    assert len(candidate_batches(selected)) == 1


def test_prepare_infers_named_regulation_instead_of_order_heading() -> None:
    document = {"blocks": [
        {"block_id": "B-1", "text": "中华人民共和国国务院令"},
        {"block_id": "B-2", "text": "第613号"},
        {"block_id": "B-3", "text": "中华人民共和国招标投标法实施条例"},
        {"block_id": "B-4", "text": "本条例自2012年2月1日起施行。"},
    ]}
    knowledge = {"legal_document": {"title": "中华人民共和国国务院令"}, "units": [unit(1, "适用本条例。")]}
    extraction = prepare_metadata_extraction(knowledge, document)
    assert extraction["basic_information"]["canonical_title"] == "中华人民共和国招标投标法实施条例"
    assert extraction["basic_information"]["document_number"] == "国务院令第613号"
    assert extraction["basic_information"]["original_effective_date"] == "2012-02-01"
    assert extraction["status"] == "pending_ai"
