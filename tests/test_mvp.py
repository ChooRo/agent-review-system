"""不依赖MinerU服务和真实大模型的MVP自检。"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from services.runtime import read_json  # noqa: E402
from services.llm import LLMService  # noqa: E402
from services.mineru import MinerUService, adapt_content_list  # noqa: E402
from services.workflow import STEPS, WorkflowEngine, structure_review_batches  # noqa: E402


def write_document(path: Path, role: str, lines: list[tuple[str, str]]) -> Path:
    """写入带稳定Block ID的最小Document JSON测试文件。"""
    blocks = []
    heading_path: list[str] = []
    for index, (block_type, text) in enumerate(lines, start=1):
        if block_type == "heading":
            heading_path = [text]
        blocks.append(
            {
                "block_id": f"{role[:1].upper()}-{index:03d}",
                "block_type": block_type,
                "heading_path": list(heading_path),
                "text": text,
                "page_no": index,
                "bbox": [10, 10, 500, 50],
                "reading_order": index,
            }
        )
    path.write_text(
        json.dumps(
            {"document_id": f"DOC-{role}", "document_role": role, "blocks": blocks},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


class WorkflowMVPTest(unittest.TestCase):
    """验证断点恢复、三个场景和证据链。"""

    def test_three_scenarios_and_resume(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            procurement = write_document(
                root / "procurement.json",
                "procurement",
                [
                    ("heading", "资格与实质性条件"),
                    ("paragraph", "供应商须具有消防检测资质。"),
                    ("heading", "技术需求与验收"),
                    ("paragraph", "供应商应在合同签订后30日内完成交付并通过验收。"),
                    ("heading", "评审办法与评分"),
                    ("paragraph", "技术评分项满分为20分。"),
                    ("heading", "商务报价与付款"),
                    ("paragraph", "最高限价为100万元，验收合格后付款。"),
                    ("heading", "合同履约与责任"),
                    ("paragraph", "中标人应提供三年质保，违约时承担相应责任。"),
                ],
            )
            response = write_document(
                root / "response.json",
                "response",
                [
                    ("heading", "资格响应"),
                    ("paragraph", "我公司具有消防检测资质并提供证书附件。"),
                    ("heading", "技术响应"),
                    ("paragraph", "我公司承诺合同签订后20日内完成交付并通过验收。"),
                    ("heading", "报价"),
                    ("paragraph", "本项目响应报价为90万元。"),
                    ("heading", "服务承诺"),
                    ("paragraph", "我公司承诺提供三年质保服务。"),
                ],
            )
            contract = write_document(
                root / "contract.json",
                "contract",
                [
                    ("heading", "合同主体"),
                    ("paragraph", "甲方为采购人，乙方为中标供应商。"),
                    ("heading", "合同金额"),
                    ("paragraph", "合同金额为90万元。"),
                    ("heading", "交付与验收"),
                    ("paragraph", "乙方应在合同签订后20日内交付并通过验收。"),
                    ("heading", "质保和违约"),
                    ("paragraph", "乙方应提供三年质保，违约时承担相应责任。"),
                ],
            )
            engine = WorkflowEngine(root / "runs", SRC_ROOT / "skills.json")
            self.assertIn("name: understand-document-structure", engine.formal_skills["structure"])
            self.assertIn("name: understand-procurement-document", engine.formal_skills["procurement"])
            self.assertIn("name: review-procurement-document", engine.formal_skills["procurement_review"])

            procurement_store = engine.start(
                "procurement",
                {"procurement": str(procurement)},
                mode="mock",
                pause_after="build_ledger",
            )
            self.assertEqual(procurement_store.load_state()["status"], "paused")
            self.assertEqual(procurement_store.load_state()["completed_steps"][-1], "build_ledger")
            profile = read_json(procurement_store.artifact_path(3, "structure_profile"))["profiles"]["procurement"]
            self.assertEqual(profile["llm_review_batch_count"], 0)
            self.assertTrue(profile["section_responsibilities"])
            trace = read_json(procurement_store.run_dir / "llm_traces" / "extract_candidates_001.json")
            payload = json.loads(trace["request"]["messages"][1]["content"])
            self.assertNotIn("global_profile", payload)
            self.assertIn("section_context", payload)
            self.assertEqual(set(payload["blocks"][0]), {"block_id", "type", "page", "text"})
            engine.resume(procurement_store.run_dir)
            self.assertEqual(procurement_store.load_state()["status"], "completed")

            response_store = engine.start(
                "response",
                {"procurement": str(procurement), "response": str(response)},
                mode="mock",
            )
            contract_store = engine.start(
                "contract",
                {
                    "procurement": str(procurement),
                    "response": str(response),
                    "contract": str(contract),
                },
                mode="mock",
            )
            for store in (response_store, contract_store):
                state = store.load_state()
                self.assertEqual(state["status"], "completed")
                self.assertEqual(state["completed_steps"], STEPS)
                report = read_json(store.artifact_path(STEPS.index("final_report") + 1, "final_report"))
                self.assertTrue(report["human_review_required"])
                self.assertTrue(store.events_path.is_file())

    def test_mineru_accepts_docx_without_conversion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "example.docx"
            source.touch()
            prepared = MinerUService()._prepare_source(source, Path(temporary) / "output")
            self.assertEqual(prepared, source)

    def test_mineru_text_level_becomes_heading_and_noise_stays_separate(self) -> None:
        document = adapt_content_list(
            [
                {"type": "text", "text": "第一章 询比公告", "text_level": 2, "page_idx": 0},
                {"type": "text", "text": "正文内容", "page_idx": 0},
                {"type": "header", "text": "重复页眉", "page_idx": 0},
                {"type": "page_number", "text": "-1-", "page_idx": 0},
            ],
            "example",
            "procurement",
        )
        self.assertEqual(
            [block["block_type"] for block in document["blocks"]],
            ["heading", "paragraph", "header", "page_number"],
        )
        self.assertEqual(document["blocks"][1]["heading_path"], ["第一章 询比公告"])

    def test_mineru_converts_legacy_doc_to_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "legacy.doc"
            source.touch()
            output = root / "output"

            def fake_convert(*args, **kwargs):
                (output / "converted" / "legacy.pdf").touch()

            with patch("services.mineru.shutil.which", return_value="soffice"), patch(
                "services.mineru.subprocess.run", side_effect=fake_convert
            ) as run:
                prepared = MinerUService()._prepare_source(source, output)
            self.assertEqual(prepared.suffix, ".pdf")
            self.assertIn("pdf", run.call_args.args[0])

    def test_only_ambiguous_structure_batches_need_llm(self) -> None:
        blocks = [
            {"block_id": "B-1", "block_type": "heading", "text": "资格条件"},
            {"block_id": "B-2", "block_type": "paragraph", "text": "供应商须提供资质。"},
            {"block_id": "B-3", "block_type": "heading", "text": "其他说明"},
            {"block_id": "B-4", "block_type": "paragraph", "text": "含义需要结合正文判断。"},
        ]
        selected = structure_review_batches(blocks, "procurement", 20)
        self.assertEqual([[b["block_id"] for b in batch] for batch in selected], [["B-3", "B-4"]])

    def test_candidate_batches_run_in_parallel(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document = write_document(
                root / "procurement.json",
                "procurement",
                [
                    ("heading", "资格条件"), ("paragraph", "供应商须具备资质。" * 600),
                    ("heading", "技术需求"), ("paragraph", "供应商应满足技术要求。" * 600),
                    ("heading", "评审办法"), ("paragraph", "本项目按照评分标准评审。" * 600),
                ],
            )
            active = 0
            maximum = 0
            lock = threading.Lock()

            def fake_call(self, step, system_prompt, payload, mock_result):
                nonlocal active, maximum
                with lock:
                    active += 1
                    maximum = max(maximum, active)
                time.sleep(0.03)
                with lock:
                    active -= 1
                return mock_result

            engine = WorkflowEngine(
                root / "runs",
                SRC_ROOT / "skills.json",
                {"workflow": {"extract_workers": 3}},
            )
            with patch.object(LLMService, "json_call", new=fake_call):
                store = engine.start(
                    "procurement",
                    {"procurement": str(document)},
                    mode="mock",
                    pause_after="extract_candidates",
                )
            self.assertGreaterEqual(maximum, 2)
            checkpoints = list((store.run_dir / "batch_artifacts" / "extract_candidates").glob("*.json"))
            self.assertGreaterEqual(len(checkpoints), 2)

            class FailIfCalled:
                def json_call(self, *args, **kwargs):
                    raise AssertionError("已成功批次不应再次调用LLM")

            cached = engine._extract_candidates(store, store.load_state(), FailIfCalled(), None)
            self.assertTrue(cached["candidates"]["procurement"])


if __name__ == "__main__":
    unittest.main()
