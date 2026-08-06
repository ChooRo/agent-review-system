"""Tool 注册、权限、检索和证据校验的最小自检。"""

import sys
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_ROOT))

from tools import ToolContext, build_registry  # noqa: E402


class ToolTest(unittest.TestCase):
    def test_allowlist_search_and_evidence_validation(self) -> None:
        registry = build_registry()
        context = ToolContext(run_id="RUN-1", agent="procurement_review_agent")
        blocks = [
            {"block_id": "B-1", "text": "合同签订后30日内交付", "page_no": 2},
            {"block_id": "B-2", "text": "质保期三年", "page_no": 3},
        ]
        search = registry.call("search_blocks", context, blocks=blocks, query="质保期")
        self.assertEqual(search.status, "success")
        self.assertEqual(search.data["blocks"][0]["block_id"], "B-2")

        evidence = registry.call(
            "validate_evidence", context,
            finding={"evidence_block_ids": ["B-2", "B-404"]},
            block_index={"B-2": {"page_no": 3, "quote": "质保期三年"}},
        )
        self.assertEqual(evidence.data["evidence_status"], "verified")
        self.assertEqual(evidence.data["invalid_block_ids"], ["B-404"])

        denied = registry.call("search_response_evidence", context, blocks=blocks, requirement_text="质保")
        self.assertEqual(denied.status, "rejected")


if __name__ == "__main__":
    unittest.main()
