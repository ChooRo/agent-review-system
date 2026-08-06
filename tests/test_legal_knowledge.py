"""法规条、款、项重组和证据继承测试。"""

import sys
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_ROOT))

from services.legal_knowledge import build_legal_knowledge  # noqa: E402
from services.workflow import rank_legal_units  # noqa: E402
from tools.rule_tools import search_legal_units  # noqa: E402
from tools.schemas import ToolContext  # noqa: E402


class LegalKnowledgeTest(unittest.TestCase):
    def test_article_paragraph_item_and_evidence(self) -> None:
        texts = [
            "示例法", "目录", "第一章 总则", "第一条 本法用于测试：",
            "（一）第一种情况；", "（二）第二种情况。", "前款另有规定的，从其规定。",
            "第二条 不得伪造证据。",
        ]
        document = {
            "document_id": "example",
            "source_file": "example.docx",
            "parser": {"name": "test"},
            "blocks": [{
                "block_id": f"legal:B-{index:05d}", "text": text,
                "page_no": None, "bbox": None,
            } for index, text in enumerate(texts, 1)],
        }
        result = build_legal_knowledge(document, {"title": "示例法", "status": "effective", "effective_date": "2020-01-01"})
        self.assertEqual(result["quality"]["article_count"], 2)
        self.assertEqual(result["quality"]["unit_count"], 5)
        item = next(unit for unit in result["units"] if unit.get("item_no") == "一")
        self.assertEqual(item["paragraph_no"], 1)
        self.assertIn("本法用于测试", item["search_text"])
        self.assertEqual(item["evidence"][0]["block_id"], "legal:B-00005")
        search = search_legal_units(
            context=ToolContext(run_id="law-test"), units=result["units"],
            query=item["text"], top_k=3,
        )
        self.assertEqual(search["units"][0]["legal_unit_id"], item["legal_unit_id"])

        ranked = rank_legal_units(
            result["units"],
            [{"statement": "供应商不得伪造投标证明材料"}],
            top_k=2,
        )
        self.assertEqual(ranked[0]["article_no"], "第二条")
        self.assertGreater(ranked[0]["retrieval_score"], 0)


if __name__ == "__main__":
    unittest.main()
