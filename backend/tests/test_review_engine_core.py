"""Migrated core checks for the authoritative backend review engine."""

from app.review_engine.services.legal_knowledge import build_legal_knowledge
from app.review_engine.services.workflow import rank_legal_units, structure_review_batches
from app.review_engine.tools import ToolContext, build_registry
from app.review_engine.tools.rule_tools import search_legal_units
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
