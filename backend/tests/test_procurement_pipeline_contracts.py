import json
from pathlib import Path

from app.review_engine.services.procurement.batching import (
    BatchAssembler, BatchBudget, BatchValidator, LogicalUnitBuilder,
    rows_html, table_business_view, table_rows, token_estimate,
)
from app.review_engine.services.procurement.evidence import EvidenceValidationService
from app.review_engine.services.procurement.ledger import LedgerService
from app.review_engine.services.procurement.quality import (
    QualityCheckService,
    supplement_damaged_table_text,
    table_content_quality_flags,
)
from app.review_engine.services.procurement.candidates import (
    collect_system_warnings, deterministic_hard_facts, extraction_batch_payload,
    merge_candidate_items, validate_candidate_items,
)
from app.review_engine.services.procurement.structure import (
    deterministic_references, structure_context_for_blocks,
)
from app.review_engine.services.procurement.workflow import (
    PROCUREMENT_EXTRACTION_CONTRACT, REVIEW_TOPICS, WorkflowEngine,
)


def test_system_quality_warnings_are_separate_and_deduplicated() -> None:
    warning = {"finding_type": "parse_quality", "title": "表格未识别", "evidence_block_ids": ["B-1"]}
    result = collect_system_warnings(
        {"quality": {"procurement": {"quality_findings": [warning, warning]}}},
        {"extraction_findings": [{"finding_type": "extraction_quality", "title": "批次失败"}]},
    )
    assert [item["title"] for item in result] == ["表格未识别", "批次失败"]
    assert all(item["review_scope"] == "system_quality" for item in result)


def test_compliance_matrix_reviews_all_seven_topics_and_builds_candidates(tmp_path: Path) -> None:
    engine = WorkflowEngine(
        tmp_path / "runs", Path(__file__).parents[1] / "app" / "review_engine" / "skills.json",
        {"workflow": {"review_workers": 2}},
    )

    class LLM:
        @staticmethod
        def json_call(_step, _prompt, payload):
            finding = []
            if payload["topic"] == "资格与实质性条件":
                finding = [{
                    "finding_type": "legal_risk", "title": "资格材料缺失", "risk_level": "medium",
                    "evidence_block_ids": ["B-1"], "legal_unit_ids": ["L-1"],
                }]
            return {"coverage_status": "reviewed", "checks": [], "candidate_findings": finding}

    class Store:
        @staticmethod
        def event(*_args, **_kwargs): return None

    previous = {
        "build_scene_view": {"topic_views": {topic: [] for topic in REVIEW_TOPICS}},
        "build_ledger": {"ledgers": {"procurement": {"source_assertions": [{
            "category": "资格与实质性条件", "statement": "供应商须提供营业执照",
            "evidence_block_ids": ["B-1"],
        }]}}},
        "match_legal_applicability": {"applicable_legal_units": [{
            "legal_unit_id": "L-1", "search_text": "供应商营业执照", "text": "供应商应提供营业执照",
        }]},
        "match_rules": {"rules": []}, "global_validation": {"issues": []},
    }
    engine._previous = lambda _store, step: previous[step]
    result = engine._build_compliance_matrix(Store(), {}, LLM(), None)
    assert [item["topic"] for item in result["coverage_matrix"]] == list(REVIEW_TOPICS)
    assert result["reviewed_topic_count"] == 7
    assert result["candidate_findings"][0]["candidate_id"].startswith("CND-")


def document(blocks):
    return {"document_id": "doc", "document_role": "procurement", "blocks": blocks}


def test_procurement_extraction_skill_is_compact_and_keeps_hard_guards(tmp_path: Path) -> None:
    skills = Path(__file__).parents[1] / "app" / "review_engine" / "skills.json"
    prompt = WorkflowEngine(tmp_path / "runs", skills).formal_skills["procurement"] + PROCUREMENT_EXTRACTION_CONTRACT
    assert token_estimate(prompt) <= 800
    for guard in ("一个候选只表达一个主要动作", "连续原文", "repeated_context", "完整业务行", "不得进行合规审查"):
        assert guard in prompt


def test_quality_gate_and_logical_batch_coverage() -> None:
    blocks = [
        {"block_id": "procurement:B-1", "block_type": "heading", "heading_level": 1, "heading_path": ["技术需求"], "text": "技术需求", "page_no": 1, "reading_order": 1},
        {"block_id": "procurement:B-2", "block_type": "paragraph", "heading_path": ["技术需求"], "text": "第一条 供应商须提供服务。", "page_no": 1, "reading_order": 2},
        {"block_id": "procurement:B-3", "block_type": "table", "heading_path": ["技术需求"], "text": "参数|要求", "page_no": 2, "reading_order": 3, "source": {"table_html": "<table><tr><td>参数</td></tr></table>"}},
    ]
    quality = QualityCheckService().check(document(blocks))
    assert quality["status"] in {"passed", "degraded"}
    logical = LogicalUnitBuilder().build(document(blocks))
    batches = BatchAssembler(BatchBudget(1000, 100, 100, 0)).assemble(logical)
    validation = BatchValidator().validate(logical, batches)
    assert validation["status"] == "passed"
    assert validation["primary_block_count"] == 3


def test_quality_prepare_excludes_empty_and_repeated_edge_noise() -> None:
    blocks = []
    order = 0
    for page in range(1, 4):
        for block_type, text in (("paragraph", "采购文件.pdf"), ("paragraph", f"第{page}页有效正文")):
            order += 1
            blocks.append({"block_id": f"B-{order}", "block_type": block_type, "text": text, "page_no": page, "reading_order": order})
    blocks.extend([
        {"block_id": "B-EMPTY", "block_type": "paragraph", "text": "", "page_no": 3, "reading_order": 99},
        {"block_id": "B-IMAGE", "block_type": "image", "text": "", "page_no": 3, "reading_order": 100},
        {"block_id": "B-TABLE", "block_type": "table", "text": "", "page_no": 3, "reading_order": 101, "source": {}},
    ])
    prepared, actions = QualityCheckService().prepare(document(blocks))
    excluded = {item["block_id"] for item in actions}
    assert {"B-1", "B-3", "B-5", "B-EMPTY", "B-IMAGE", "B-TABLE"} <= excluded
    assert next(item for item in actions if item["block_id"] == "B-IMAGE")["route"] == "review_finding"
    logical = LogicalUnitBuilder().build(prepared)
    assert excluded.isdisjoint(logical["block_owner"])


def test_table_rows_preserve_line_breaks_and_business_provenance() -> None:
    block = {
        "block_id": "T-1", "block_type": "table", "page_no": 3,
        "source": {"table_html": (
            "<table><tr><th>类别</th><th>评审标准</th></tr>"
            "<tr><td rowspan='2'>技术评审</td><td>第一项<br>第二项</td></tr>"
            "<tr><td>第三项</td></tr></table>"
        )},
    }
    rows = table_rows(block)
    assert rows[1]["cells"][1] == "第一项\n第二项"
    assert rows[2]["cells"] == ["技术评审", "第三项"]
    assert rows[2]["inherited_columns"] == [0]
    view = table_business_view(block)
    assert view["records"][1]["values"] == {"类别": "技术评审", "评审标准": "第三项"}
    assert view["source"] == {"block_id": "T-1", "page": 3}
    assert view["records"][1]["inherited_columns"] == [0]
    assert view["structure_confidence"] == 0.8
    assert "第一项<br>第二项" in rows_html(rows[:1], rows[1:2])


def test_table_rows_keeps_continuation_rows_with_omitted_trailing_cells() -> None:
    block = {
        "block_id": "procurement:B-1",
        "source": {"table_html": (
            "<table><tr><th rowspan='2'>类别</th><th>项目</th><th>标准</th></tr>"
            "<tr><td>服务</td><td>须提供承诺函</td></tr>"
            "<tr><td colspan='2'>续表内容</td></tr></table>"
        )},
    }

    rows = table_rows(block)

    assert [len(row["cells"]) for row in rows] == [3, 3, 3]
    assert rows[-1]["cells"] == ["续表内容", "", ""]


def test_ai_payload_uses_business_table_json_instead_of_duplicate_flat_text() -> None:
    payload = extraction_batch_payload("procurement", [{
        "block_id": "T-1", "block_type": "table", "page_no": 3, "role": "primary",
        "text": "类别 | 分值\n技术 | 4", "heading_path": ["评审标准"],
        "table_html": "<table><tr><th>类别</th><th>分值</th></tr><tr><td>技术</td><td>4</td></tr></table>",
    }])
    block = payload["blocks"][0]
    assert "tbl" in block and "x" not in block
    assert block["tbl"]["records"][0]["values"] == {"类别": "技术", "分值": "4"}


def test_suspicious_table_text_and_missing_item_breaks_trigger_retry() -> None:
    block = {
        "block_id": "T-BAD", "block_type": "table", "page_no": 1,
        "text": "采购人发出澄清或者修截改的止时间和发方式",
        "source": {"table_html": (
            "<table><tr><th>内容</th></tr><tr><td>采购人发出澄清或者修截改的止时间和发方式"
            "（1）事项一（2）事项二（3）事项三</td></tr></table>"
        )},
    }
    flags = table_content_quality_flags(block)
    assert {"suspected_inserted_character", "suspected_missing_character"} <= set(flags)
    report = QualityCheckService().check(document([block]))
    assert report["status"] == "retryable"
    assert any(issue["code"] == "TABLE_CONTENT_QUALITY" for issue in report["issues"])


def test_content_warning_keeps_structured_table_for_review_with_warning() -> None:
    block = {
        "block_id": "T-RETAIN", "block_type": "table", "page_no": 1,
        "text": "采购人发出修截改的止时间和发方式",
        "source": {"table_html": (
            "<table><tr><th>条款号</th><th>编列内容</th></tr>"
            "<tr><td>2.4.2</td><td>采购人发出修截改的止时间和发方式</td></tr></table>"
        )},
    }
    checker = QualityCheckService()
    prepared_document = document([block])
    report = checker.check(prepared_document)
    report = checker.degrade_to_review(prepared_document, report)
    retained = prepared_document["blocks"][0]
    assert retained["block_type"] == "table"
    assert retained["quality_reason"] == "table_content_degraded"
    assert any(item["action"] == "retained_with_quality_warning" for item in report["actions"])


def test_native_table_fallback_skips_structured_content_warning(tmp_path: Path) -> None:
    structured = {
        "block_id": "T-STRUCTURED", "block_type": "table", "page_no": 13,
        "source": {"table_html": (
            "<table><tr><th>条款号</th><th>编列内容</th></tr>"
            "<tr><td>2.4.2</td><td>修截改的止时间和发方式</td></tr></table>"
        )},
    }
    broken = {
        "block_id": "T-BROKEN", "block_type": "table", "page_no": 14,
        "text": "表格内容缺失",
    }

    class FakeMinerU:
        def __init__(self) -> None:
            self.pages: set[int] | None = None

        def supplement_native_pdf_pages(self, document, source, pages, *, reason):
            self.pages = set(pages)

    mineru = FakeMinerU()
    supplement_damaged_table_text(
        mineru,
        document([structured, broken]),
        tmp_path / "source.pdf",
        {"issues": [
            {"code": "TABLE_CONTENT_QUALITY", "block_ids": ["T-STRUCTURED"]},
            {"code": "TABLE_CONTENT_QUALITY", "block_ids": ["T-BROKEN"]},
        ]},
    )
    assert mineru.pages == {14}


def test_attachment_target_may_be_a_paragraph_block() -> None:
    report = QualityCheckService().check(document([
        {"block_id": "B-1", "block_type": "paragraph", "text": "详细清单详见附件一。", "page_no": 1},
        {"block_id": "B-2", "block_type": "paragraph", "text": "附件一消防设施检测清单", "page_no": 2},
    ]))
    assert not any(issue["code"] == "ATTACHMENT_REQUIRED_MISSING" for issue in report["issues"])


def test_table_structure_failure_retries_with_high_effort_hybrid(tmp_path: Path) -> None:
    skills = Path(__file__).parents[1] / "app" / "review_engine" / "skills.json"
    engine = WorkflowEngine(tmp_path / "runs", skills)
    bad = document([{
        "block_id": "T-1", "block_type": "table", "text": "\n".join(f"{i} | {'参数' * 30}" for i in range(20)),
        "page_no": 1, "source": {},
    }])
    engine._previous = lambda _store, _step: {"documents": {"procurement": bad}}
    calls = []

    class Store:
        run_dir = tmp_path
        @staticmethod
        def event(*_args, **_kwargs): return None
        @staticmethod
        def write_artifact(*_args, **_kwargs): return None

    class MinerU:
        backend = "pipeline"
        effort = "medium"
        @staticmethod
        def parse(*_args, **kwargs):
            calls.append(kwargs)
            return document([{"block_id": "B-1", "block_type": "paragraph", "text": "完整正文。", "page_no": 1}])

    result = engine._quality_check(Store(), {"documents": {"procurement": str(tmp_path / "input.pdf")}}, None, MinerU())
    assert result["quality"]["procurement"]["status"] == "passed"
    assert calls == [{
        "parse_method": "auto", "backend": "hybrid-engine", "effort": "high",
        "start_page_id": 0, "end_page_id": 0,
    }]


def test_empty_table_routes_directly_to_page_ocr(tmp_path: Path) -> None:
    skills = Path(__file__).parents[1] / "app" / "review_engine" / "skills.json"
    engine = WorkflowEngine(tmp_path / "runs", skills)
    bad = document([{
        "block_id": "T-1", "block_type": "table", "text": "", "page_no": 1,
        "reading_order": 1, "source": {},
    }])
    engine._previous = lambda _store, _step: {"documents": {"procurement": bad}}
    calls = []

    class Store:
        run_dir = tmp_path
        @staticmethod
        def event(*_args, **_kwargs): return None
        @staticmethod
        def write_artifact(*_args, **_kwargs): return None

    class MinerU:
        backend = "pipeline"
        effort = "medium"
        @staticmethod
        def parse(*_args, **kwargs):
            calls.append(kwargs)
            return document([{
                "block_id": "O-1", "block_type": "paragraph",
                "text": "采购人发出询比文件修改的截止时间和发布方式。",
                "page_no": 1, "reading_order": 1,
            }])

    result = engine._quality_check(
        Store(), {"documents": {"procurement": str(tmp_path / "input.pdf")}}, None, MinerU()
    )
    report = result["quality"]["procurement"]
    assert report["status"] == "passed"
    assert [call["parse_method"] for call in calls] == ["ocr"]
    assert report["retry"]["attempts"][0]["accepted"] is True


def test_structure_only_table_uses_hybrid_then_ocr_if_needed(tmp_path: Path) -> None:
    skills = Path(__file__).parents[1] / "app" / "review_engine" / "skills.json"
    engine = WorkflowEngine(tmp_path / "runs", skills)
    bad = document([{
        "block_id": "T-1", "block_type": "table",
        "text": "\n".join(f"{index} | {'技术参数' * 25}" for index in range(20)),
        "page_no": 1, "reading_order": 1, "source": {},
    }])
    engine._previous = lambda _store, _step: {"documents": {"procurement": bad}}
    calls = []

    class Store:
        run_dir = tmp_path
        @staticmethod
        def event(*_args, **_kwargs): return None
        @staticmethod
        def write_artifact(*_args, **_kwargs): return None

    class MinerU:
        backend = "pipeline"
        effort = "medium"
        @staticmethod
        def parse(*_args, **kwargs):
            calls.append(kwargs)
            if kwargs["parse_method"] == "auto":
                return document([{
                    "block_id": "H-1", "block_type": "header", "text": "项目名称",
                    "page_no": 1, "reading_order": 1,
                }])
            return document([{
                "block_id": "O-1", "block_type": "paragraph", "text": "完整表格正文。",
                "page_no": 1, "reading_order": 1,
            }])

    result = engine._quality_check(
        Store(), {"documents": {"procurement": str(tmp_path / "input.pdf")}}, None, MinerU()
    )
    assert result["quality"]["procurement"]["status"] == "passed"
    assert [call["parse_method"] for call in calls] == ["auto", "ocr"]


def test_unrecoverable_table_becomes_unknown_finding_without_blocking(tmp_path: Path) -> None:
    skills = Path(__file__).parents[1] / "app" / "review_engine" / "skills.json"
    engine = WorkflowEngine(tmp_path / "runs", skills)
    bad = document([{
        "block_id": "T-1", "block_type": "table", "text": "\n".join(f"{i} | {'参数' * 30}" for i in range(20)),
        "page_no": 7, "source": {},
    }])
    engine._previous = lambda _store, _step: {"documents": {"procurement": bad}}

    class Store:
        run_dir = tmp_path
        @staticmethod
        def event(*_args, **_kwargs): return None
        @staticmethod
        def write_artifact(*_args, **_kwargs): return None

    class MinerU:
        backend = "pipeline"
        effort = "medium"
        @staticmethod
        def parse(*_args, **_kwargs): return document([dict(bad["blocks"][0])])

    result = engine._quality_check(Store(), {"documents": {"procurement": str(tmp_path / "input.pdf")}}, None, MinerU())
    report = result["quality"]["procurement"]
    assert report["status"] == "degraded"
    assert report["quality_findings"][0]["risk_level"] == "unknown"
    assert any(action["reason"] == "table_structure_unreliable" for action in report["actions"])


def test_hybrid_disconnect_falls_back_to_degraded_review(tmp_path: Path) -> None:
    skills = Path(__file__).parents[1] / "app" / "review_engine" / "skills.json"
    engine = WorkflowEngine(tmp_path / "runs", skills)
    bad = document([{
        "block_id": "T-1", "block_type": "table", "text": "\n".join(f"{i} | {'参数' * 30}" for i in range(20)),
        "page_no": 7, "source": {},
    }])
    engine._previous = lambda _store, _step: {"documents": {"procurement": bad}}

    class Store:
        run_dir = tmp_path
        @staticmethod
        def event(*_args, **_kwargs): return None
        @staticmethod
        def write_artifact(*_args, **_kwargs): return None

    class MinerU:
        backend = "pipeline"
        effort = "medium"
        @staticmethod
        def parse(*_args, **_kwargs): raise ConnectionError("server disconnected")

    result = engine._quality_check(Store(), {"documents": {"procurement": str(tmp_path / "input.pdf")}}, None, MinerU())
    report = result["quality"]["procurement"]
    assert report["status"] == "degraded"
    assert report["retry"]["status"] == "failed"
    assert report["retry"]["error_type"] == "ConnectionError"
    assert report["quality_findings"][0]["risk_level"] == "unknown"


def test_dense_flattened_table_is_blocked_instead_of_reviewed_as_text() -> None:
    table = {
        "block_id": "T-FLAT", "block_type": "table", "page_no": 1, "reading_order": 1,
        "text": "\n".join(f"{index} | {'技术参数' * 25}" for index in range(20)), "source": {},
    }
    quality = QualityCheckService().check(document([table]))
    assert quality["status"] == "retryable"
    logical = LogicalUnitBuilder().build(document([table]))
    batches = BatchAssembler().assemble(logical)
    validation = BatchValidator().validate(logical, batches)
    assert validation["status"] == "failed"
    assert any(issue["code"] == "TABLE_STRUCTURE_UNRELIABLE" for issue in validation["issues"])


def test_ocr_status_is_not_reported_as_zero_percent_failure() -> None:
    source = document([{"block_id": "B-1", "block_type": "paragraph", "text": "有效正文", "page_no": 1}])
    source["parser"] = {"parse_method": "auto"}
    assert QualityCheckService().check(source)["ocr_status"] == "not_assessed"
    source["parser"]["parse_method"] = "ocr"
    assert QualityCheckService().check(source)["ocr_status"] == "unavailable"


def test_clause_units_follow_reading_order_without_inventing_number_levels() -> None:
    blocks = [
        {"block_id": "B-4", "block_type": "paragraph", "heading_path": ["资格要求"], "text": "1. 提供营业执照", "page_no": 1, "reading_order": 4},
        {"block_id": "B-1", "block_type": "heading", "heading_level": 1, "heading_path": ["资格要求"], "text": "资格要求", "page_no": 1, "reading_order": 1},
        {"block_id": "B-3", "block_type": "paragraph", "heading_path": ["资格要求"], "text": "（一）具有独立承担民事责任的能力", "page_no": 1, "reading_order": 3},
        {"block_id": "B-2", "block_type": "paragraph", "heading_path": ["资格要求"], "text": "供应商应满足以下要求：", "page_no": 1, "reading_order": 2},
        {"block_id": "B-5", "block_type": "paragraph", "heading_path": ["资格要求"], "text": "二、具有良好商业信誉", "page_no": 1, "reading_order": 5},
    ]
    manifest = LogicalUnitBuilder().build(document(blocks))
    clause = next(unit for unit in manifest["units"] if unit["unit_type"] == "clause_unit")
    assert clause["ordered_block_ids"] == ["B-3", "B-4", "B-5"]
    assert clause["relation_mode"] == "sequential"
    assert clause["hierarchy_status"] == "not_inferred"
    assert "B-2" in clause["context_block_ids"]
    assert [block["block_id"] for block in clause["blocks"][:2]] == ["B-1", "B-2"]
    assert [block["numbering_text"] for block in clause["blocks"] if block["role"] == "primary"] == ["（一）", "1.", "二、"]


def test_article_boundary_table_exit_and_attachment_exit_are_deterministic() -> None:
    clause_blocks = [
        {"block_id": "C-1", "block_type": "paragraph", "heading_path": ["合同"], "text": "第一条 服务内容", "page_no": 1, "reading_order": 1},
        {"block_id": "C-2", "block_type": "paragraph", "heading_path": ["合同"], "text": "（一）提供检测服务", "page_no": 1, "reading_order": 2},
        {"block_id": "C-3", "block_type": "paragraph", "heading_path": ["合同"], "text": "第二条 服务期限", "page_no": 1, "reading_order": 3},
    ]
    clauses = [unit for unit in LogicalUnitBuilder().build(document(clause_blocks))["units"] if unit["unit_type"] == "clause_unit"]
    assert [unit["primary_block_ids"] for unit in clauses] == [["C-1", "C-2"], ["C-3"]]

    table_blocks = [
        {"block_id": "T-1", "block_type": "table", "heading_path": ["报价"], "text": "名称|价格", "page_no": 1, "reading_order": 1},
        {"block_id": "T-2", "block_type": "paragraph", "heading_path": ["报价"], "text": "备注：报价含税", "page_no": 1, "reading_order": 2},
        {"block_id": "T-3", "block_type": "paragraph", "heading_path": ["报价"], "text": "供应商还应提交报价说明。", "page_no": 1, "reading_order": 3},
    ]
    table_units = LogicalUnitBuilder().build(document(table_blocks))["units"]
    assert table_units[0]["primary_block_ids"] == ["T-1", "T-2"]
    assert table_units[1]["primary_block_ids"] == ["T-3"]

    attachment_blocks = [
        {"block_id": "A-1", "block_type": "heading", "heading_level": 1, "heading_path": ["附件一"], "text": "附件一 报价表", "page_no": 1, "reading_order": 1},
        {"block_id": "A-2", "block_type": "heading", "heading_level": 1, "heading_path": ["附件一"], "text": "一、填写说明", "page_no": 1, "reading_order": 2},
        {"block_id": "A-3", "block_type": "paragraph", "heading_path": ["附件一"], "text": "请据实填写。", "page_no": 1, "reading_order": 3},
        {"block_id": "A-4", "block_type": "heading", "heading_level": 1, "heading_path": ["其他事项"], "text": "其他事项", "page_no": 2, "reading_order": 4},
    ]
    attachment_manifest = LogicalUnitBuilder().build(document(attachment_blocks))
    attachment_units = attachment_manifest["units"]
    assert attachment_units[0]["unit_type"] == "attachment_unit"
    assert attachment_units[0]["primary_block_ids"] == ["A-1", "A-2", "A-3"]
    assert attachment_units[1]["unit_type"] == "section_unit"
    assert attachment_units[1]["primary_block_ids"] == ["A-4"]
    attachment_batches = BatchAssembler(BatchBudget(1000, 100, 100, 0)).assemble(attachment_manifest)
    assert [
        [unit["unit_type"] for unit in batch["logical_units"]]
        for batch in attachment_batches["batches"]
    ] == [["attachment_unit"], ["section_unit"]]
    assert BatchValidator().validate(attachment_manifest, attachment_batches)["status"] == "passed"


def test_structure_context_is_filtered_by_real_block_ids() -> None:
    profile = {
        "quality_status": "passed",
        "section_responsibilities": [
            {"block_id": "B-1", "responsibility": "资格与实质性条件"},
            {"block_id": "B-9", "responsibility": "合同履约与责任"},
        ],
        "references": [
            {"source_block_ids": ["B-1"], "target_block_ids": ["B-8"], "status": "resolved"},
        ],
        "global_constraints": [
            {"constraint_type": "deadline", "evidence_block_ids": ["B-9"]},
        ],
    }
    context = structure_context_for_blocks(profile, {"B-1"})
    assert context == {
        "references": [
            {"source_block_ids": ["B-1"], "target_block_ids": ["B-8"], "status": "resolved"},
        ],
    }
    assert structure_context_for_blocks(profile, {"B-2"}) == {}


def test_oversized_html_table_splits_on_complete_rows_and_repeats_header() -> None:
    header = "<tr><th>序号</th><th>技术要求</th></tr>"
    html_rows = [f"<tr><td>{index}</td><td>{'检测要求' * 12}{index}</td></tr>" for index in range(1, 9)]
    html = f"<table>{header}{''.join(html_rows)}</table>"
    text = "\n".join(["序号 | 技术要求", *(f"{index} | {'检测要求' * 12}{index}" for index in range(1, 9))])
    logical = LogicalUnitBuilder().build(document([{
        "block_id": "T-LONG",
        "block_type": "table",
        "heading_path": ["技术需求"],
        "text": text,
        "page_no": 1,
        "reading_order": 1,
        "source": {"table_html": html},
    }]))
    batches = BatchAssembler(BatchBudget(600, 400, 20, 20)).assemble(logical)
    validation = BatchValidator().validate(logical, batches)
    fragments = [
        block for batch in batches["batches"] for block in batch["blocks"]
        if block.get("block_id") == "T-LONG" and block.get("role") == "primary"
    ]
    assert validation["status"] == "passed"
    assert len(fragments) > 1
    assert all(block["text"].startswith("序号 | 技术要求") for block in fragments)
    assert [block["table_fragment"]["row_start"] for block in fragments] == [1, 3, 5, 7]
    assert [block["table_fragment"]["row_end"] for block in fragments] == [2, 4, 6, 8]
    assert validation["primary_block_count"] == 1


def test_cross_page_table_blocks_split_rows_without_changing_evidence_ids() -> None:
    def table(block_id: str, page: int, start: int) -> dict:
        header = "<tr><th>序号</th><th>要求</th></tr>"
        rows = "".join(
            f"<tr><td>{index}</td><td>供应商须提交材料{index}</td></tr>"
            for index in range(start, start + 3)
        )
        return {
            "block_id": block_id, "block_type": "table", "heading_path": ["资格要求"],
            "text": "序号 | 要求\n" + "\n".join(
                f"{index} | 供应商须提交材料{index}" for index in range(start, start + 3)
            ),
            "page_no": page, "reading_order": page,
            "source": {"table_html": f"<table>{header}{rows}</table>"},
        }

    logical = LogicalUnitBuilder().build(document([table("T-1", 1, 1), table("T-2", 2, 4)]))
    batches = BatchAssembler(BatchBudget(1000, 400, 100, 0, 25, 20, 2)).assemble(logical)
    validation = BatchValidator().validate(logical, batches)
    fragments = [
        block for batch in batches["batches"] for block in batch["blocks"]
        if block.get("role") == "primary" and block.get("table_fragment")
    ]
    assert validation["status"] == "passed"
    assert {block["block_id"] for block in fragments} == {"T-1", "T-2"}
    assert len({block["table_fragment"]["fragment_id"] for block in fragments}) == len(fragments)
    assert all(batch["table_row_count"] <= 2 for batch in batches["batches"])
    continuation = [
        block for batch in batches["batches"] for block in batch["blocks"]
        if block.get("role") == "repeated_context" and block.get("table_fragment", {}).get("continuation_context")
    ]
    assert continuation and continuation[0]["table_fragment"]["source_block_id"] == "T-1"


def test_indivisible_oversized_table_row_remains_a_hard_error() -> None:
    html = f"<table><tr><th>技术要求</th></tr><tr><td>{'超长要求' * 80}</td></tr></table>"
    logical = LogicalUnitBuilder().build(document([{
        "block_id": "T-ROW",
        "block_type": "table",
        "text": f"技术要求\n{'超长要求' * 80}",
        "page_no": 1,
        "reading_order": 1,
        "source": {"table_html": html},
    }]))
    batches = BatchAssembler(BatchBudget(140, 30, 20, 20)).assemble(logical)
    validation = BatchValidator().validate(logical, batches)
    assert validation["status"] == "failed"
    assert any(issue["code"] == "TOKEN_LIMIT" for issue in validation["issues"])


def test_batches_limit_primary_blocks_and_estimated_candidates() -> None:
    blocks = [
        {
            "block_id": f"B-{index}", "block_type": "paragraph", "heading_path": ["资格要求"],
            "text": f"{index}. 供应商须提交第{index}项证明。", "page_no": 1, "reading_order": index,
        }
        for index in range(1, 31)
    ]
    logical = LogicalUnitBuilder().build(document(blocks))
    budget = BatchBudget(
        model_tokens=16_000, output_tokens=3_000, safety_tokens=1_000,
        input_overhead_tokens=9_000, primary_block_limit=25, candidate_limit=20, table_row_limit=16,
    )
    batches = BatchAssembler(budget).assemble(logical)
    validation = BatchValidator().validate(logical, batches)
    assert validation["status"] == "passed"
    assert len(batches["batches"]) == 2
    assert all(batch["primary_block_count"] <= 25 for batch in batches["batches"])
    assert all(batch["candidate_estimate"] <= 20 for batch in batches["batches"])


def test_business_understanding_receives_batch_structure_context(tmp_path: Path) -> None:
    skills = Path(__file__).parents[1] / "app" / "review_engine" / "skills.json"
    engine = WorkflowEngine(tmp_path / "runs", skills, {"workflow": {
        "rerun_batches": [1], "rerun_output_tokens": 5_000, "rerun_stream": False,
    }})
    captured = {}

    class Store:
        run_dir = tmp_path

        @staticmethod
        def event(*_args, **_kwargs):
            return None

    class LLM:
        @staticmethod
        def json_call(_step, _prompt, payload, **kwargs):
            captured.update(payload)
            captured["max_tokens"] = kwargs["max_tokens"]
            captured["stream"] = kwargs["stream"]
            return {"candidate_items": []}

    previous = {
        "assemble_review_batches": {
            "manifests": {
                "procurement": {
                    "validation": {"status": "passed"},
                    "batches": [{
                        "batch_no": 1,
                        "purpose": "procurement_understanding",
                        "coverage_strategy": "complete_logical_units",
                        "logical_units": [{"unit_id": "LU-1", "unit_type": "clause_unit", "relation_mode": "sequential"}],
                        "blocks": [{
                            "block_id": "B-1",
                            "type": "table",
                            "page": 1,
                            "text": "材料 | 营业执照",
                            "role": "primary",
                            "table_fragment": {"fragment_no": 1, "row_start": 1, "row_end": 1},
                        }],
                    }],
                }
            }
        },
        "structure_profile": {
            "profiles": {
                "procurement": {
                    "quality_status": "passed",
                    "section_responsibilities": [{"block_id": "B-1", "responsibility": "资格与实质性条件"}],
                    "references": [{"source_block_ids": ["B-1"], "target_block_ids": ["B-8"]}],
                }
            }
        },
    }
    engine._previous = lambda _store, step: previous[step]
    engine._extract_candidates(Store(), {}, LLM(), None)
    assert "logical_units" not in captured
    assert captured["structure_context"] == {
        "references": [{"source_block_ids": ["B-1"], "target_block_ids": ["B-8"]}],
    }
    assert captured["blocks"][0]["r"] == "primary"
    assert captured["blocks"][0]["tf"]["row_start"] == 1
    assert captured["max_tokens"] == 5_000
    assert captured["stream"] is False


def test_invalid_candidate_is_retried_once_before_ledger_admission(tmp_path: Path) -> None:
    skills = Path(__file__).parents[1] / "app" / "review_engine" / "skills.json"
    engine = WorkflowEngine(tmp_path / "runs", skills)
    calls = []

    class Store:
        run_dir = tmp_path
        @staticmethod
        def event(*_args, **_kwargs):
            return None

    class LLM:
        @staticmethod
        def json_call(step, _prompt, _payload, **_kwargs):
            calls.append(step)
            if step == "extract_candidates":
                return {"candidate_items": [{"statement": "在", "evidence_block_ids": [], "evidence_quote": ""}]}
            return {"candidate_items": [{
                "statement": "供应商须提供营业执照",
                "evidence_block_ids": ["B-1"],
                "evidence_quote": "供应商须提供营业执照",
            }]}

    previous = {
        "assemble_review_batches": {"manifests": {"procurement": {"validation": {"status": "passed"}, "batches": [{
            "batch_no": 1, "purpose": "procurement_understanding", "coverage_strategy": "complete_logical_units",
            "logical_units": [], "blocks": [{"block_id": "B-1", "type": "paragraph", "page": 1, "text": "供应商须提供营业执照", "role": "primary"}],
        }]}}},
        "structure_profile": {"profiles": {"procurement": {}}},
    }
    engine._previous = lambda _store, step: previous[step]
    result = engine._extract_candidates(Store(), {}, LLM(), None)
    assert calls == ["extract_candidates", "extract_candidates_retry"]
    assert [item["statement"] for item in result["candidates"]["procurement"]] == ["供应商须提供营业执照"]


def test_candidate_retry_disconnect_keeps_initial_valid_items(tmp_path: Path) -> None:
    skills = Path(__file__).parents[1] / "app" / "review_engine" / "skills.json"
    engine = WorkflowEngine(tmp_path / "runs", skills)
    retry_payload = {}

    class Store:
        run_dir = tmp_path
        @staticmethod
        def event(*_args, **_kwargs): return None

    class LLM:
        @staticmethod
        def json_call(step, _prompt, payload, **_kwargs):
            if step == "extract_candidates_retry":
                retry_payload.update(payload)
                raise RuntimeError("LLM call failed")
            return {"candidate_items": [
                {"statement": "供应商须提供营业执照", "evidence_block_ids": ["B-1"], "evidence_quote": "供应商须提供营业执照"},
                {"statement": "在", "evidence_block_ids": [], "evidence_quote": ""},
            ]}

    previous = {
        "assemble_review_batches": {"manifests": {"procurement": {"validation": {"status": "passed"}, "batches": [{
            "batch_no": 1, "purpose": "procurement_understanding", "coverage_strategy": "complete_logical_units",
            "logical_units": [{"unit_id": "LU-1", "primary_block_ids": ["B-1"]}],
            "blocks": [{"block_id": "B-1", "type": "paragraph", "page": 1, "text": "供应商须提供营业执照", "role": "primary"}],
        }]}}},
        "structure_profile": {"profiles": {"procurement": {}}},
    }
    engine._previous = lambda _store, step: previous[step]

    result = engine._extract_candidates(Store(), {}, LLM(), None)

    assert [item["statement"] for item in result["candidates"]["procurement"]] == ["供应商须提供营业执照"]
    assert result["status"] == "completed"
    assert result["batch_reports"]["procurement"][0]["failure"] is None
    assert "logical_units" not in retry_payload and "structure_context" not in retry_payload
    checkpoint = json.loads((tmp_path / "batch_artifacts" / "extract_candidates" / "procurement_001.json").read_text(encoding="utf-8"))
    assert checkpoint["status"] == "completed" and checkpoint["accepted"][0]["statement"] == "供应商须提供营业执照"


def test_one_failed_extraction_batch_does_not_abort_other_batches(tmp_path: Path) -> None:
    skills = Path(__file__).parents[1] / "app" / "review_engine" / "skills.json"
    engine = WorkflowEngine(tmp_path / "runs", skills, {"workflow": {"extract_workers": 2}})

    class Store:
        run_dir = tmp_path
        @staticmethod
        def event(*_args, **_kwargs): return None

    class LLM:
        @staticmethod
        def json_call(_step, _prompt, payload, **_kwargs):
            if payload["batch_no"] == 1:
                raise RuntimeError("LLM call failed")
            return {"candidate_items": [{
                "statement": "供应商须按期履约", "evidence_block_ids": ["B-2"], "evidence_quote": "供应商须按期履约",
            }]}

    batches = [
        {"batch_no": 1, "purpose": "procurement_understanding", "coverage_strategy": "complete_logical_units", "logical_units": [],
         "blocks": [{"block_id": "B-1", "type": "paragraph", "page": 1, "text": "失败批次原文", "role": "primary"}]},
        {"batch_no": 2, "purpose": "procurement_understanding", "coverage_strategy": "complete_logical_units", "logical_units": [],
         "blocks": [{"block_id": "B-2", "type": "paragraph", "page": 2, "text": "供应商须按期履约", "role": "primary"}]},
    ]
    previous = {
        "assemble_review_batches": {"manifests": {"procurement": {"validation": {"status": "passed"}, "batches": batches}}},
        "structure_profile": {"profiles": {"procurement": {}}},
    }
    engine._previous = lambda _store, step: previous[step]

    result = engine._extract_candidates(Store(), {}, LLM(), None)

    assert result["status"] == "degraded"
    assert [item["statement"] for item in result["candidates"]["procurement"]] == ["供应商须按期履约"]
    assert [report["status"] for report in result["batch_reports"]["procurement"]] == ["degraded", "completed"]
    assert result["extraction_findings"][0]["risk_level"] == "unknown"


def test_ledger_preserves_occurrences_and_evidence_validation_checks_quotes() -> None:
    candidate = {"category": "技术需求与验收", "statement": "供应商须提供服务", "subject": "供应商", "action": "提供", "object": "服务", "evidence_block_ids": ["procurement:B-1"], "evidence_quote": "供应商须提供服务", "source_batch": 1}
    ledger = LedgerService().build("procurement", [candidate, candidate], "DV-1")
    assert len(ledger["extraction_occurrences"]) == 2
    assert len(ledger["source_assertions"]) == 1
    result = EvidenceValidationService().validate(
        {"title": "服务", "evidence_block_ids": ["procurement:B-1"], "evidence_quotes": ["不存在的摘录"]},
        {"procurement:B-1": {"full_text": "供应商须提供服务", "page_no": 1, "bbox": [0, 0, 1, 1], "quote": "供应商须提供服务"}}, {}, {},
    )
    assert result["evidence_status"] == "evidence_insufficient"
    assert result["errors"] == ["quote_mismatch", "conclusion_support_uncertain"]
    assert result["evidence"][0]["quote"] == "供应商须提供服务"


def test_business_candidates_require_complete_matching_evidence() -> None:
    blocks = [{"block_id": "procurement:B-1", "text": "★服务地点：湖北省武汉市采购人指定地点。"}]
    candidates = [
        {"statement": "服务地点为武汉市", "evidence_block_ids": ["wrong-id"], "evidence_quote": "**服务地点：湖北省武汉市采购人指定地点**"},
        {"statement": "服务期限两年", "evidence_block_ids": ["wrong-id"], "evidence_quote": "服务期限：两年"},
        {"statement": "模型摘录有误", "evidence_block_ids": ["procurement:B-1"], "evidence_quote": "不存在的摘录"},
    ]
    accepted, rejected = validate_candidate_items(candidates, {"procurement:B-1"}, blocks)
    assert len(rejected) == 2
    assert accepted[0]["evidence_status"] == "verified"
    assert accepted[0]["evidence_block_ids"] == ["procurement:B-1"]
    assert {item["reason"] for item in rejected} == {"evidence_block_required", "evidence_quote_mismatch"}


def test_business_candidate_accepts_quote_spanning_listed_blocks() -> None:
    blocks = [
        {"block_id": "B-heading", "text": "供应商不得存在下列情形之一"},
        {"block_id": "B-item", "text": "（1）被依法暂停投标资格的；"},
    ]
    candidates = [{
        "statement": "供应商不得被依法暂停投标资格。",
        "evidence_block_ids": ["B-heading", "B-item"],
        "evidence_quote": "供应商不得存在下列情形之一\n（1）被依法暂停投标资格的；",
    }]

    accepted, rejected = validate_candidate_items(candidates, {"B-heading", "B-item"}, blocks)

    assert rejected == []
    assert accepted[0]["evidence_block_ids"] == ["B-heading", "B-item"]


def test_candidate_gate_rejects_fragments_and_symbols() -> None:
    blocks = [{"block_id": "B-1", "text": "供应商须提供营业执照。"}]
    candidates = [
        {"statement": "在", "evidence_block_ids": [], "evidence_quote": ""},
        {"statement": "★", "evidence_block_ids": ["B-1"], "evidence_quote": "供应商须提供营业执照。"},
        {"statement": "供应商须符合相关要求，并在", "evidence_block_ids": ["B-1"], "evidence_quote": "供应商须提供营业执照。"},
    ]
    accepted, rejected = validate_candidate_items(candidates, {"B-1"}, blocks)
    assert accepted == []
    assert len(rejected) == 3


def test_ledger_does_not_conflict_duration_with_location_or_reference() -> None:
    candidates = [
        {"category": "项目与日程", "statement": "服务期为2年", "subject": "供应商", "action": "提供服务", "object": "检测服务", "source_value": "2年", "evidence_block_ids": ["B-1"], "evidence_quote": "服务期为2年", "evidence_status": "verified"},
        {"category": "项目与日程", "statement": "服务地点为武汉市", "subject": "供应商", "action": "提供服务", "object": "检测服务", "source_value": "武汉市", "evidence_block_ids": ["B-2"], "evidence_quote": "服务地点为武汉市", "evidence_status": "verified"},
        {"category": "商务报价与付款", "statement": "最高响应限价详见第一章", "subject": "供应商", "action": "报价", "object": "响应报价", "source_value": "详见第一章", "evidence_block_ids": ["B-3"], "evidence_quote": "最高响应限价详见第一章", "evidence_status": "verified"},
        {"category": "商务报价与付款", "statement": "最高响应限价为200943.40元", "subject": "供应商", "action": "报价", "object": "响应报价", "source_value": "200943.40元", "evidence_block_ids": ["B-4"], "evidence_quote": "最高响应限价为200943.40元", "evidence_status": "verified"},
    ]
    ledger = LedgerService().build("procurement", candidates, "DV-1")
    assert all(relation["relation_type"] != "conflicting" for cluster in ledger["business_item_clusters"] for relation in cluster["relations"])


def test_full_document_reference_resolver_finds_chapter_and_clause() -> None:
    blocks = [
        {"block_id": "B-1", "block_type": "heading", "text": "第一章 询比公告"},
        {"block_id": "B-2", "block_type": "paragraph", "text": "1.3 最高响应限价为200943.40元"},
        {"block_id": "B-3", "block_type": "paragraph", "text": "最高响应限价详见第一章第1.3款。"},
    ]
    references = deterministic_references(blocks)
    assert references
    assert any(item["status"] == "resolved" and "B-2" in item["target_block_ids"] for item in references)


def test_evidence_validator_handles_chinese_titles_and_verified_absence() -> None:
    validator = EvidenceValidationService()
    payment = validator.validate(
        {"finding_type": "ambiguity", "title": "付款比例条款逻辑异常", "evidence_block_ids": ["procurement:B-1"], "evidence_quotes": ["合同签订后支付合同价的30%"]},
        {"procurement:B-1": {"full_text": "合同签订后支付合同价的 30%；初验后支付 60%", "page_no": 1, "quote": "付款方式"}}, {}, {},
    )
    missing = validator.validate(
        {"finding_type": "missing_element", "title": "缺失资格章节", "absence_check_verified": True}, {}, {}, {},
    )
    assert payment["evidence_status"] == "verified"
    assert missing["evidence_status"] == "verified" and missing["validation_basis"] == "absence_check"


def test_table_quotes_and_hard_facts_use_the_same_canonical_text() -> None:
    table = {
        "block_id": "procurement:B-1", "type": "table", "page": 1,
        "text": "<table><tr><td><strong>项目编号</strong></td><td>XHCG-2026-017</td></tr>"
                "<tr><td>采购方式</td><td>竞争性磋商</td></tr>"
                "<tr><td>响应截止时间</td><td>2026年9月18日 10:00（北京时间）</td></tr></table>",
        "role": "primary",
    }
    facts = deterministic_hard_facts("procurement", [table])
    assert {item["requirement_type"] for item in facts} == {
        "project_code", "procurement_method", "submission_deadline",
    }
    merged = merge_candidate_items([
        {"requirement_type": "procurement_method", "evidence_block_ids": ["procurement:B-1"]}
    ], facts)
    assert sum(item.get("requirement_type") == "procurement_method" for item in merged) == 1

    result = EvidenceValidationService().validate(
        {
            "title": "评分细则缺乏量化标准",
            "evidence_block_ids": ["procurement:B-1"],
            "evidence_quotes": ["项目编号 | XHCG-2026-017\n采购方式 | 竞争性磋商"],
        },
        {"procurement:B-1": {"full_text": table["text"], "page_no": 1}}, {}, {},
    )
    assert result["evidence_status"] == "evidence_insufficient"
    assert result["quote_mismatches"] == []
    assert "conclusion_support_uncertain" in result["errors"]


def test_final_report_marks_disabled_legal_pipeline_as_degraded(tmp_path: Path) -> None:
    skills = Path(__file__).parents[1] / "app" / "review_engine" / "skills.json"
    engine = WorkflowEngine(tmp_path / "runs", skills)
    previous = {
        "validate_evidence": {
            "overall_conclusion": "模型原始总结", "findings": [{}],
            "verified_count": 1, "insufficient_count": 0,
        },
        "build_ledger": {"stats": {"procurement": {"assertions": 3}}},
        "build_scene_view": {"topic_views": {"项目与日程": [], "评审办法与评分": []}},
        "match_rules": {
            "execution_status": "degraded", "degraded_reasons": ["executable_rule_library_empty"]
        },
        "match_legal_applicability": {
            "execution_status": "degraded", "degraded_reasons": ["legal_applicability_disabled"],
            "task_legal_facts": {}, "decisions": [], "frozen_context": [], "warnings": [],
        },
    }
    engine._previous = lambda _store, step: previous[step]
    report = engine._final_report(None, {"run_id": "R-1", "scenario": "procurement"}, None, None)
    assert report["pipeline_status"] == "degraded"
    assert {item["step"] for item in report["degraded_steps"]} == {
        "match_rules", "match_legal_applicability",
    }
    assert "覆盖2类主题" in report["overall_conclusion"]
    assert report["agent_overall_conclusion"] == "模型原始总结"


def test_final_report_includes_degraded_parse_quality(tmp_path: Path) -> None:
    skills = Path(__file__).parents[1] / "app" / "review_engine" / "skills.json"
    engine = WorkflowEngine(tmp_path / "runs", skills)
    previous = {
        "validate_evidence": {"overall_conclusion": "初审", "findings": [], "verified_count": 0, "insufficient_count": 0},
        "build_ledger": {"stats": {}}, "build_scene_view": {"topic_views": {}},
        "match_rules": {"execution_status": "completed"},
        "match_legal_applicability": {"execution_status": "completed", "task_legal_facts": {}, "decisions": [], "frozen_context": [], "warnings": []},
        "quality_check": {"quality": {"procurement": {"status": "degraded", "issues": [{"code": "EMPTY_PAGES", "severity": "warning"}]}}},
    }
    engine._previous = lambda _store, step: previous[step]
    report = engine._final_report(None, {"run_id": "R-1", "scenario": "procurement"}, None, None)
    assert report["pipeline_status"] == "degraded"
    assert report["degraded_steps"] == [{"step": "quality_check", "reasons": ["EMPTY_PAGES"]}]
