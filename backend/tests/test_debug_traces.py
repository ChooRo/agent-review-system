import json

from app.services.procurement.review import ProcurementReviewService


def test_debug_llm_calls_infers_step_from_trace_filename(tmp_path) -> None:
    trace_dir = tmp_path / "llm_traces"
    trace_dir.mkdir()
    (trace_dir / "extract_candidates_001.json").write_text(
        json.dumps({"request": {"messages": []}, "response": "{}"}),
        encoding="utf-8",
    )

    calls = ProcurementReviewService._debug_llm_calls(tmp_path)

    assert calls[0]["step"] == "extract_candidates"


def test_debug_keeps_parse_stage_when_later_stage_has_failed(tmp_path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "01_parse_documents.json").write_text(
        json.dumps({"documents": {"procurement": {"blocks": [{"block_id": "procurement:B-1"}]}}}),
        encoding="utf-8",
    )
    stages = ProcurementReviewService._debug_stage_results(tmp_path)
    assert [stage["key"] for stage in stages] == ["mineru_parse"]
    assert stages[0]["data"]["documents"]["procurement"]["blocks"][0]["block_id"] == "procurement:B-1"


def test_debug_unavailable_explains_engine_run_mapping() -> None:
    result = ProcurementReviewService._debug_unavailable(
        "task-1", "engine-1", "failed", "RUN_DIRECTORY_UNAVAILABLE", "shared run directory is not visible"
    )
    assert result["engine_run_id"] == "engine-1"
    assert result["diagnosis"]["code"] == "RUN_DIRECTORY_UNAVAILABLE"
