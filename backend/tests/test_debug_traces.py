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
