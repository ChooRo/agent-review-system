"""权威后端审查引擎的迁移后核心检查。"""

from app.review_engine.services.legal.knowledge import build_legal_knowledge
from app.review_engine.services.procurement.ledger import LedgerService
from app.review_engine.services.legal.retrieval import rank_legal_units
from app.review_engine.services.procurement.structure import structure_review_batches
from app.review_engine.services.topics import canonical_topic
from app.review_engine.tools import ToolContext, build_registry
from app.review_engine.tools.legal.rules import search_legal_units
from app.review_engine.tools.legal.rules import search_rules
from app.review_engine.services.mineru import MinerUService, adapt_content_list
from pathlib import Path


def test_migrated_legal_knowledge_retrieval_and_tool_allowlist() -> None:
    texts = ["示例法", "目录", "第一章 总则", "第一条 本法用于测试：", "（一）第一种情况；", "（二）第二种情况。", "前款另有规定的，从其规定。", "第二条 不得伪造证据。"]
    document = {"document_id": "example", "blocks": [{"block_id": f"legal:B-{i:05d}", "text": text, "page_no": 1, "bbox": [0, 0, 1, 1]} for i, text in enumerate(texts, 1)]}
    knowledge = build_legal_knowledge(document, {"title": "示例法", "status": "effective"})
    assert knowledge["quality"]["article_count"] == 2 and knowledge["quality"]["unit_count"] == 5
    unit = next(item for item in knowledge["units"] if item.get("item_no") == "一")
    assert unit["paragraph_no"] == 1 and "本法用于测试" in unit["search_text"]
    found = search_legal_units(context=ToolContext(run_id="law-test"), units=knowledge["units"], query=unit["text"], top_k=1)
    assert found["units"][0]["legal_unit_id"] == unit["legal_unit_id"]
    assert "retrieval_signals" in found["units"][0]
    assert rank_legal_units(knowledge["units"], [{"statement": "供应商不得伪造投标证明材料"}], 1)[0]["article_no"] == "第二条"
    registry = build_registry()
    result = registry.call("search_legal_units", ToolContext(run_id="law-test", agent="procurement_review_agent"), units=knowledge["units"], query=unit["text"], top_k=1)
    assert result.status == "success"


def test_migrated_parser_and_structure_helpers() -> None:
    document = adapt_content_list([{"type": "text", "text": "第一章 询比公告", "text_level": 2, "page_idx": 0}, {"type": "text", "text": "正文内容", "page_idx": 0}, {"type": "header", "text": "页眉", "page_idx": 0}], "example", "procurement")
    assert [block["block_type"] for block in document["blocks"]] == ["heading", "paragraph", "header"]
    assert document["blocks"][1]["heading_path"] == ["第一章 询比公告"]
    source = Path(__file__).with_suffix(".docx")
    assert MinerUService()._prepare_source(source, source.parent).suffix == ".docx"
    blocks = [{"block_id": "B-1", "block_type": "heading", "text": "资格条件"}, {"block_id": "B-2", "block_type": "paragraph", "text": "供应商须提供资质。"}, {"block_id": "B-3", "block_type": "heading", "text": "其他说明"}, {"block_id": "B-4", "block_type": "paragraph", "text": "需要结合正文判断。"}]
    assert [[item["block_id"] for item in batch] for batch in structure_review_batches(blocks, "procurement", 20)] == [["B-3", "B-4"]]


def test_controlled_topics_join_procurement_assertions_and_legal_units() -> None:
    ledger = LedgerService().build("procurement", [{
        "category": "商务报价与付款",
        "category_tags": ["商务报价与付款"],
        "requirement_type": "bid_bond",
        "statement": "供应商应提交响应保证金",
        "evidence_block_ids": ["B-1"],
        "evidence_quote": "供应商应提交响应保证金",
    }], "DV-1")
    assertion = ledger["source_assertions"][0]
    assert assertion["requirement_type"] == "bid_bond"
    assert assertion["category_tags"] == ["商务报价与付款"]
    assert assertion["topics"][0]["key"] == "bid_bond"

    document = {"document_id": "bond-law", "blocks": [
        {"block_id": "legal:B-1", "text": "保证金规定", "page_no": 1},
        {"block_id": "legal:B-2", "text": "第一条 投标保证金不得超过规定比例。", "page_no": 1},
    ]}
    unit = build_legal_knowledge(document, {"status": "effective"})["units"][0]
    assert unit["topics"] == [{"key": "bid_bond", "source": "dictionary", "matched_terms": ["投标保证金"]}]
    assert rank_legal_units([unit], [assertion], 1)[0]["retrieval_signals"]["topic_exact"] == ["bid_bond"]


def test_unknown_requirement_type_is_preserved_only_as_source_metadata() -> None:
    ledger = LedgerService().build("procurement", [{
        "requirement_type": "model_invented_type",
        "statement": "普通说明",
        "evidence_block_ids": ["B-1"],
        "evidence_quote": "普通说明",
    }], "DV-1")
    assertion = ledger["source_assertions"][0]
    assert canonical_topic("model_invented_type") == "other"
    assert assertion["requirement_type"] == "other"
    assert assertion["attributes"]["original_requirement_type"] == "model_invented_type"


def test_legal_ranking_uses_topic_then_synonym_then_article_then_bigram() -> None:
    units = [
        {"legal_unit_id": "exact", "article_no": "第一条", "search_text": "完全无关", "topics": [{"key": "bid_bond"}]},
        {"legal_unit_id": "synonym", "article_no": "第二条", "search_text": "质保义务", "topics": [{"key": "warranty"}]},
        {"legal_unit_id": "article", "article_no": "第三条", "search_text": "其他义务", "topics": []},
        {"legal_unit_id": "fallback", "article_no": "第四条", "search_text": "高度相似文本", "topics": []},
    ]
    items = [
        {"requirement_type": "bid_bond", "topics": [{"key": "bid_bond"}], "statement": "结构化主题"},
        {"statement": "质保期要求"},
        {"statement": "请核对第三条"},
        {"statement": "高度相似文本"},
    ]
    ranked = rank_legal_units(units, items, 4)
    assert [unit["legal_unit_id"] for unit in ranked] == ["exact", "synonym", "article", "fallback"]
    assert [unit["retrieval_score"] for unit in ranked] == [4.0, 3.0, 2.0, 1.0]


def test_search_rules_uses_published_module_and_tags_schema() -> None:
    rules = [
        {"id": "hit", "status": "published", "module": "procurement", "tags": ["保证金"]},
        {"id": "global", "status": "published", "module": "procurement", "tags": []},
        {"id": "pending", "status": "pending_confirmation", "module": "procurement", "tags": ["保证金"]},
        {"id": "wrong-module", "status": "published", "module": "contract", "tags": ["保证金"]},
        {"id": "wrong-tag", "status": "published", "module": "procurement", "tags": ["付款"]},
    ]
    result = search_rules(
        context=ToolContext(run_id="rule-test"), rules=rules,
        scenario="procurement", text="检查投标保证金", top_k=10,
    )
    assert [rule["id"] for rule in result["rules"]] == ["hit", "global"]
    assert result["matched_count"] == 2
