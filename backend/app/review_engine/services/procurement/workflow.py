"""全文理解、业务台账和三个审查Agent的可断点MVP流水线。"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

from ..llm import LLMService
from ..mineru import MinerUService
from ..runtime import RunStore, read_json, write_json
from ..topics import canonical_topic, dictionary_topics, topic_keys
from ...tools.legal.rules import search_rules
from ...tools.schemas import ToolContext
from .batching import BatchAssembler, BatchBudget, BatchValidator, LogicalUnitBuilder, token_estimate
from .evidence import EvidenceValidationService, canonical_evidence_text, plain_evidence_text
from .ledger import LedgerService, SceneViewService
from .quality import QualityCheckService, QualityGateError
from app.core.config import get_settings
from app.repositories.rule_repository import RuleRepository


STEPS = [
    "parse_documents",
    "quality_check",
    "structure_profile",
    "build_logical_units",
    "assemble_review_batches",
    "extract_candidates",
    "build_ledger",
    "build_scene_view",
    "global_validation",
    "derive_legal_facts",
    "match_rules",
    "match_legal_applicability",
    "build_compliance_matrix",
    "agent_review",
    "validate_evidence",
    "final_report",
]
REQUIRED_ROLES = {"procurement": {"procurement"}}
ID_PREFIX = {"procurement": "REQ"}
LEGAL_FACT_FIELDS = (
    "project_type", "procurement_method", "is_government_procurement",
    "is_engineering_related", "is_mandatory_tender", "region", "review_stage",
)
REVIEW_TOPICS = (
    "项目与日程", "技术需求与验收", "资格与实质性条件", "商务报价与付款",
    "附件与引用", "评审办法与评分", "合同履约与责任",
)
TOPIC_FOCUS = {
    "项目与日程": "采购方式 公告 澄清 修改 截止时间 开标 服务期限",
    "技术需求与验收": "技术标准 交付 验收 方法 指标",
    "资格与实质性条件": "供应商资格 业绩 禁止条件 证明材料",
    "商务报价与付款": "预算 最高限价 报价 税率 付款 保证金",
    "附件与引用": "附件 引用 格式 响应文件",
    "评审办法与评分": "评标委员会 评分标准 否决 评审 中标 成交",
    "合同履约与责任": "合同 履约 验收 付款 违约 责任",
}
PROCUREMENT_EXTRACTION_CONTRACT = """
\n输入blocks短键：id=block_id，t=type，p=page，r=内容角色，x=原文，tf=表格分片。
返回严格JSON：{"candidate_items":[{"primary_category":"","requirement_type":"","statement":"","evidence_block_ids":[""],"evidence_quote":""}]}。
顶层只能有candidate_items；可选字段仅在有原文依据且非空时输出。
"""


class WorkflowEngine:
    """执行九步审查流水线，并在每一步完成后保存可恢复检查点。"""
    # ponytail: keep this migrated workflow intact for traceability; split parse/ledger/review stages only after their backend contracts stabilize.

    def __init__(
        self,
        runs_root: Path,
        skills_path: Path,
        config: dict[str, Any] | None = None,
        progress_callback: Callable[[RunStore, dict[str, Any], str | None], None] | None = None,
    ):
        self.runs_root = runs_root.resolve()
        self.skills = read_json(skills_path)
        self.config = config or {}
        self.progress_callback = progress_callback
        formal_root = skills_path.parent / "skills"
        self.formal_skills = {
            "structure": load_formal_skill(formal_root / "understand-document-structure"),
            # Extraction runs once per batch. Keep the reusable rules, but do not resend the
            # long reference examples on every stateless request.
            "procurement": load_formal_skill(
                formal_root / "understand-procurement-document", include_references=False
            ),
            "procurement_review": load_formal_skill(formal_root / "review-procurement-document"),
        }

    def start(
        self,
        scenario: str,
        documents: dict[str, str],
        pause_after: str | None = None,
        task_context: dict[str, Any] | None = None,
    ) -> RunStore:
        """校验输入、创建运行并执行到完成或指定断点。"""
        validate_request(scenario, documents, pause_after)
        store = RunStore.create(self.runs_root, scenario, documents, pause_after, task_context)
        return self.run(store)

    def resume(self, run_dir: Path, pause_after: str | None = None) -> RunStore:
        """从最后一个成功步骤后继续；可设置新的暂停点。"""
        store = RunStore(run_dir)
        state = store.load_state()
        if state["status"] == "completed":
            return store
        if pause_after is not None:
            if pause_after not in STEPS:
                raise ValueError(f"未知断点：{pause_after}")
            state["pause_after"] = pause_after
            store.save_state(state)
        store.event("INFO", "run", "resumed", "从检查点恢复运行")
        return self.run(store)

    def run(self, store: RunStore) -> RunStore:
        """按固定顺序执行未完成步骤，失败时保留现场。"""
        state = store.load_state()
        llm = LLMService(self.config.get("llm", {}), store)
        mineru_config = self.config.get("mineru", {})
        mineru = MinerUService(
            mineru_config.get("api_url", "http://127.0.0.1:8000"),
            int(mineru_config.get("timeout_seconds", 900)),
            self.config.get("ocr", {}),
            str(mineru_config.get("backend") or "pipeline"),
            str(mineru_config.get("effort") or "medium"),
        )
        handlers: dict[str, Callable[[RunStore, dict[str, Any], LLMService, MinerUService], Any]] = {
            name: getattr(self, f"_{name}") for name in STEPS
        }
        state["status"] = "running"
        state["error"] = None
        store.save_state(state)
        if self.progress_callback:
            self.progress_callback(store, state, None)
        for index, step in enumerate(STEPS, start=1):
            state = store.load_state()
            if step in state["completed_steps"]:
                continue
            state["current_step"] = step
            store.save_state(state)
            started = time.perf_counter()
            store.event("INFO", step, "started", "步骤开始")
            try:
                output = handlers[step](store, state, llm, mineru)
                artifact = store.write_artifact(index, step, output)
                state = store.load_state()
                state["completed_steps"].append(step)
                state["current_step"] = None
                state["error"] = None
                duration = round(time.perf_counter() - started, 3)
                store.save_state(state)
                if self.progress_callback:
                    self.progress_callback(store, state, step)
                store.event(
                    "INFO",
                    step,
                    "completed",
                    "步骤完成",
                    duration_seconds=duration,
                    artifact=str(artifact),
                    summary=summarize(output),
                )
                if state.get("pause_after") == step:
                    state["status"] = "paused"
                    store.save_state(state)
                    store.event("INFO", step, "paused", "命中断点，等待恢复")
                    return store
            except Exception as exc:
                state = store.load_state()
                state["status"] = "failed"
                state["error"] = {"step": step, "type": type(exc).__name__, "message": str(exc)}
                store.save_state(state)
                store.event("ERROR", step, "failed", str(exc), error_type=type(exc).__name__)
                logging.getLogger("review_mvp").exception("步骤失败：%s", step)
                return store
        state = store.load_state()
        state["status"] = "completed"
        state["current_step"] = None
        store.save_state(state)
        store.event("INFO", "run", "completed", "全部步骤完成")
        return store

    def _previous(self, store: RunStore, step: str) -> Any:
        """读取指定步骤的检查点产物。"""
        index = STEPS.index(step) + 1
        return store.read_artifact(index, step)

    def _parse_documents(
        self, store: RunStore, state: dict[str, Any], llm: LLMService, mineru: MinerUService
    ) -> dict[str, Any]:
        """解析场景所需文件并建立统一Block。"""
        documents: dict[str, Any] = {}
        for role, path in state["documents"].items():
            store.event("INFO", "parse_documents", "document_started", f"解析{role}文件", source=path)
            document = mineru.parse(Path(path), store.run_dir / "mineru" / role, role)
            documents[role] = document
            store.event(
                "INFO",
                "parse_documents",
                "document_completed",
                f"{role}解析完成",
                blocks=len(document.get("blocks", [])),
            )
        return {"documents": documents}

    def _quality_check(
        self, store: RunStore, state: dict[str, Any], llm: LLMService, mineru: MinerUService
    ) -> dict[str, Any]:
        """执行四态解析质量门禁；retryable 自动以 OCR 模式重试一次。"""
        parsed = self._previous(store, "parse_documents")
        reports: dict[str, Any] = {}
        checker = QualityCheckService()
        mineru_config = self.config.get("mineru", {})
        for role, document in parsed["documents"].items():
            prepared, actions = checker.prepare(document)
            report = checker.check(prepared)
            report["actions"] = actions
            parsed["documents"][role] = prepared
            if report["status"] == "retryable" and Path(state["documents"][role]).suffix.lower() != ".json":
                table_failure = any(issue.get("code") == "TABLE_STRUCTURE" for issue in report.get("issues", []))
                retry_backend = str(mineru_config.get("table_retry_backend") or "hybrid-engine") if table_failure else mineru.backend
                retry_effort = str(mineru_config.get("table_retry_effort") or "high") if table_failure else mineru.effort
                retry_method = "auto" if table_failure else "ocr"
                store.event(
                    "WARNING", "quality_check", "retry_started",
                    f"{role}解析质量可重试，切换 {retry_backend}/{retry_method} 模式",
                    backend=retry_backend, parse_method=retry_method, effort=retry_effort,
                )
                try:
                    if table_failure:
                        ranges = _table_retry_ranges(prepared, report)
                        reparsed = prepared
                        for start_page, end_page in ranges:
                            partial = mineru.parse(
                                Path(state["documents"][role]),
                                store.run_dir / "mineru_retry" / role / f"pages_{start_page}_{end_page}", role,
                                parse_method=retry_method, backend=retry_backend, effort=retry_effort,
                                start_page_id=start_page - 1, end_page_id=end_page - 1,
                            )
                            reparsed = _merge_page_retry(reparsed, partial, start_page, end_page, role)
                    else:
                        ranges = []
                        reparsed = mineru.parse(
                            Path(state["documents"][role]), store.run_dir / "mineru_retry" / role, role,
                            parse_method=retry_method, backend=retry_backend, effort=retry_effort,
                        )
                    reparsed, retry_actions = checker.prepare(reparsed)
                    retry_report = checker.check(reparsed)
                    retry_report["retry"] = {
                        "attempted": True, "status": "completed", "backend": retry_backend,
                        "parse_method": retry_method, "effort": retry_effort,
                        "previous_status": report["status"],
                        "page_ranges": ranges,
                    }
                    retry_report["actions"] = retry_actions
                    parsed["documents"][role] = reparsed
                    report = retry_report
                except Exception as exc:
                    report["retry"] = {
                        "attempted": True, "status": "failed", "backend": retry_backend,
                        "parse_method": retry_method, "effort": retry_effort,
                        "previous_status": report["status"], "error_type": type(exc).__name__,
                    }
                    store.event(
                        "WARNING", "quality_check", "retry_failed",
                        f"{retry_backend} 重解析失败，回退首次解析并生成无法判断问题",
                        error_type=type(exc).__name__,
                    )
            if report["status"] != "unreliable" and any(
                issue.get("review_route") == "review_finding" for issue in report.get("issues", [])
            ):
                report = checker.degrade_to_review(parsed["documents"][role], report)
            store.write_artifact(1, "parse_documents", parsed)
            reports[role] = report
            if report["status"] in {"retryable", "unreliable"}:
                write_json(store.artifact_path(STEPS.index("quality_check") + 1, "quality_check"), {"quality": reports, "gate": "blocked"})
                raise QualityGateError(report)
        return {"quality": reports}

    def _build_logical_units(
        self, store: RunStore, state: dict[str, Any], llm: LLMService, mineru: MinerUService
    ) -> dict[str, Any]:
        """重建条款、表格、图片、附件、章节和段落逻辑单元。"""
        parsed = self._previous(store, "parse_documents")
        return {"manifests": {role: LogicalUnitBuilder().build(document) for role, document in parsed["documents"].items()}}

    def _assemble_review_batches(
        self, store: RunStore, state: dict[str, Any], llm: LLMService, mineru: MinerUService
    ) -> dict[str, Any]:
        """按模型 Token 预算装批并执行硬失败校验。"""
        logical = self._previous(store, "build_logical_units")["manifests"]
        config = self.config.get("workflow", {})
        budget = _batch_budget(config)
        manifests, validations = {}, {}
        for role, logical_manifest in logical.items():
            manifest = BatchAssembler(budget).assemble(logical_manifest)
            validation = BatchValidator().validate(logical_manifest, manifest)
            manifests[role], validations[role] = manifest, validation
            write_json(store.run_dir / "batch_artifacts" / "review_batches" / f"{role}.json", {**manifest, "validation": validation})
            if validation["status"] == "failed":
                raise ValueError(f"Review Batch 校验失败：{validation['issues']}")
        return {"manifests": manifests, "validations": validations}

    def _structure_profile(
        self, store: RunStore, state: dict[str, Any], llm: LLMService, mineru: MinerUService
    ) -> dict[str, Any]:
        """生成章节树、文档统计和可供后续批次共享的全局画像。"""
        parsed = self._previous(store, "parse_documents")
        profiles: dict[str, Any] = {}
        quality = self._previous(store, "quality_check")["quality"]
        for role, document in parsed["documents"].items():
            headings = [
                {
                    "block_id": b["block_id"],
                    "text": b.get("text"),
                    "heading_path": b.get("heading_path", []),
                    "page_no": b.get("page_no"),
                }
                for b in document.get("blocks", [])
                if b.get("block_type") == "heading" and b.get("text")
            ]
            responsibilities = [
                {
                    "block_id": heading["block_id"],
                    "heading": heading["text"],
                    "responsibility": responsibility,
                }
                for heading in headings
                if (responsibility := classify_structure_heading(role, str(heading["text"])))
            ]
            base = {
                "document_id": document.get("document_id"),
                "document_role": role,
                "outline": headings,
                "block_count": len(document.get("blocks", [])),
                "table_count": sum(b.get("block_type") == "table" for b in document.get("blocks", [])),
                "image_count": sum(b.get("block_type") == "image" for b in document.get("blocks", [])),
                "structure_source": "deterministic_outline",
                "section_responsibilities": responsibilities,
                "inventories": deterministic_inventories(document.get("blocks", [])),
                "clause_relations": deterministic_clause_relations(document.get("blocks", [])),
                "references": deterministic_references(document.get("blocks", [])),
            }
            review_batches = structure_review_batches(document.get("blocks", []), role, 12_000)
            write_json(
                store.run_dir / "batch_artifacts" / "structure_review" / f"{role}.json",
                {"document_role": role, "purpose": "疑难结构语义理解", "batches": [batch_manifest(index, batch) for index, batch in enumerate(review_batches, start=1)]},
            )
            store.event(
                "INFO",
                "structure_profile",
                "llm_scope_selected",
                f"{role}目录骨架已生成，仅疑难批次进入LLM",
                heading_count=len(headings),
                llm_batch_count=len(review_batches),
            )
            partials = []
            for batch_no, blocks in enumerate(review_batches, start=1):
                partials.append(
                    llm.json_call(
                        "structure_profile",
                        self.formal_skills["structure"]
                        + "\n当前输入是全文中的一个完整章节批次。严格按输出契约返回JSON；只返回本批能够证明的结构事实，后端会全局合并。",
                        {
                            "document_role": role,
                            "quality_report": quality.get(role),
                            "base_outline": base["outline"],
                            "batch_no": batch_no,
                            "blocks": block_payload(blocks),
                        },
                    )
                )
            profiles[role] = merge_structure_profiles(base, partials, quality.get(role, {}))
            profiles[role]["llm_review_batch_count"] = len(review_batches)
        return {"profiles": profiles}

    def _extract_candidates(
        self, store: RunStore, state: dict[str, Any], llm: LLMService, mineru: MinerUService
    ) -> dict[str, Any]:
        """按完整章节批次提取候选原子事项，并要求每项绑定Block。"""
        batches = self._previous(store, "assemble_review_batches")["manifests"]
        profiles = self._previous(store, "structure_profile")["profiles"]
        candidates: dict[str, list[dict[str, Any]]] = {}
        workflow_config = self.config.get("workflow", {})
        workers = int(workflow_config.get("extract_workers", 3))
        max_request_tokens = int(workflow_config.get("max_request_tokens", 12_000))
        output_tokens = int(workflow_config.get("output_tokens", 3_000))
        rerun_batches = {int(value) for value in workflow_config.get("rerun_batches", [])}
        rerun_output_tokens = int(workflow_config.get("rerun_output_tokens", output_tokens))
        extract_stream = bool(workflow_config.get("extract_stream", True))
        rerun_stream = bool(workflow_config.get("rerun_stream", extract_stream))
        extract_call_options = {
            "max_tokens": output_tokens,
            "stream": extract_stream,
            "timeout_seconds": int(workflow_config.get("extract_timeout_seconds", 240)),
            "idle_timeout_seconds": int(workflow_config.get("extract_idle_timeout_seconds", 90)),
            "total_timeout_seconds": int(workflow_config.get("extract_total_timeout_seconds", 240)),
            "max_retries": int(workflow_config.get("extract_max_retries", 1)),
        }
        extraction_findings: list[dict[str, Any]] = []
        batch_reports: dict[str, list[dict[str, Any]]] = {}
        for role, review_manifest in batches.items():
            desired_budget = _batch_budget(workflow_config)
            current_budget = review_manifest.get("budget", {})
            expected_budget = {
                "input_tokens": desired_budget.input_tokens,
                "primary_block_limit": desired_budget.primary_block_limit,
                "candidate_limit": desired_budget.candidate_limit,
                "table_row_limit": desired_budget.max_table_rows,
            }
            if current_budget and any(float(current_budget.get(key) or 0) != value for key, value in expected_budget.items()):
                logical = self._previous(store, "build_logical_units")["manifests"][role]
                review_manifest = BatchAssembler(desired_budget).assemble(logical)
                validation = BatchValidator().validate(logical, review_manifest)
                if validation["status"] == "failed":
                    raise ValueError(f"按完整请求预算重装批失败：{validation['issues']}")
                review_manifest["validation"] = validation
                write_json(
                    store.run_dir / "batch_artifacts" / "review_batches" / f"{role}.json",
                    review_manifest,
                )
                store.event(
                    "INFO", "extract_candidates", "batches_reassembled_for_request_budget",
                    f"{role}已按完整请求预算重新装批",
                    batch_count=len(review_manifest.get("batches", [])),
                    **expected_budget,
                )
            skill = self.skills["document_understanding"][role]
            structure_profile = profiles.get(role, {})
            role_items: list[dict[str, Any]] = []
            sections = review_manifest.get("batches", [])
            store.event(
                "INFO",
                "extract_candidates",
                "parallel_batches_started",
                f"{role}开始并行提取原子事项",
                batch_count=len(sections),
                workers=min(workers, len(sections)),
            )

            def extract_batch(
                entry: tuple[int, dict[str, Any]]
            ) -> tuple[int, list[dict[str, Any]], list[dict[str, Any]], bool, dict[str, Any] | None]:
                batch_no, batch = entry
                batch_call_options = {
                    **extract_call_options,
                    "max_tokens": rerun_output_tokens if batch_no in rerun_batches else output_tokens,
                    "stream": rerun_stream if batch_no in rerun_batches else extract_stream,
                }
                blocks = [
                    {
                        "block_id": b["block_id"],
                        "block_type": b["type"],
                        "page_no": b["page"],
                        "text": b["text"],
                        "role": b["role"],
                        "table_fragment": b.get("table_fragment"),
                    }
                    for b in batch.get("blocks", [])
                ]
                valid_ids = {b["block_id"] for b in blocks}
                prompt = skill["instruction"]
                result_key = "items"
                if role == "procurement":
                    prompt = self.formal_skills["procurement"] + PROCUREMENT_EXTRACTION_CONTRACT
                    result_key = "candidate_items"
                prompt += ("" if role == "procurement" else " 返回严格JSON：{\"items\":[{\"category\":\"\",\"statement\":\"\",\"subject\":\"\",\"action\":\"\",\"condition\":\"\",\"value\":\"\",\"mandatory\":false,\"evidence_block_ids\":[\"B-...\"],\"evidence_quote\":\"原文摘录\"}]}。")
                structure_context = structure_context_for_blocks(structure_profile, valid_ids)
                payload = {
                    "document_role": role,
                    "allowed_categories": skill["categories"],
                    "batch_no": batch_no,
                    "batch_purpose": batch.get("purpose"),
                    "coverage_strategy": batch.get("coverage_strategy"),
                    "batch_quality": compact_batch_quality(review_manifest.get("validation")),
                    **({"structure_context": structure_context} if structure_context else {}),
                    **extraction_batch_payload(role, blocks),
                }
                fingerprint = hashlib.sha1(
                    (
                        prompt
                        + json.dumps(payload, ensure_ascii=False, sort_keys=True)
                        + json.dumps(batch_call_options, ensure_ascii=False, sort_keys=True)
                    ).encode("utf-8")
                ).hexdigest()
                request_tokens = token_estimate(prompt + json.dumps(payload, ensure_ascii=False))
                checkpoint = (
                    store.run_dir
                    / "batch_artifacts"
                    / "extract_candidates"
                    / f"{role}_{batch_no:03d}.json"
                )
                if checkpoint.is_file():
                    cached = read_json(checkpoint)
                    if (
                        cached.get("schema_version") in {6, 7, 8}
                        and cached.get("input_fingerprint") == fingerprint
                        and cached.get("status", "completed") == "completed"
                    ):
                        return (
                            batch_no, cached.get("accepted", []), cached.get("rejected", []), True,
                            cached.get("failure"),
                        )

                if request_tokens > max_request_tokens:
                    failure = {
                        "code": "REQUEST_TOKEN_LIMIT",
                        "error_type": "RequestTokenLimit",
                        "message": f"完整请求预计 {request_tokens} Token，超过上限 {max_request_tokens}",
                        "request_tokens": request_tokens,
                    }
                    write_json(checkpoint, {
                        "schema_version": 8, "status": "failed", "document_role": role,
                        "batch_no": batch_no, "input_fingerprint": fingerprint,
                        "request_tokens": request_tokens, "accepted": [], "rejected": [], "failure": failure,
                    })
                    return batch_no, [], [], False, failure

                try:
                    result = llm.json_call(
                        "extract_candidates",
                        prompt,
                        payload,
                        **batch_call_options,
                        trace_label=f"{role}_b{batch_no:03d}",
                        trace_context={
                            "phase": "model_initial",
                            "document_role": role,
                            "batch_no": batch_no,
                            "primary_block_count": batch.get("primary_block_count", len(batch.get("primary_block_ids", []))),
                            "candidate_estimate": batch.get("candidate_estimate"),
                            "table_row_count": batch.get("table_row_count"),
                            "request_tokens": request_tokens,
                        },
                    )
                except RuntimeError as exc:
                    cause = exc.__cause__ or exc
                    failure = {
                        "code": "LLM_BATCH_UNAVAILABLE",
                        "error_type": type(cause).__name__,
                        "message": "模型服务暂时不可用，本批未形成自动提取结论",
                        "request_tokens": request_tokens,
                    }
                    write_json(checkpoint, {
                        "schema_version": 8, "status": "failed", "document_role": role,
                        "batch_no": batch_no, "input_fingerprint": fingerprint,
                        "request_tokens": request_tokens, "accepted": [], "rejected": [], "failure": failure,
                    })
                    return batch_no, [], [], False, failure
                model_items = result.get(result_key, [])
                items = merge_candidate_items(model_items, deterministic_hard_facts(role, blocks))
                accepted, rejected = validate_candidate_items(items, valid_ids, blocks)
                retryable = [item for item in rejected if item.get("retryable")]
                failure = None
                if retryable:
                    retry_ids = {
                        block_id for item in retryable for block_id in item.get("evidence_block_ids", [])
                        if block_id in valid_ids
                    }
                    retry_blocks = [block for block in payload["blocks"] if block["id"] in retry_ids]
                    if not retry_blocks and token_estimate(json.dumps(payload["blocks"], ensure_ascii=False)) <= 2_000:
                        retry_blocks = payload["blocks"]
                    if retry_blocks:
                        write_json(checkpoint, {
                            "schema_version": 8, "status": "retry_pending", "document_role": role,
                            "batch_no": batch_no, "input_fingerprint": fingerprint,
                            "request_tokens": request_tokens, "accepted": accepted, "rejected": rejected,
                        })
                        retry_payload = {
                            "document_role": role,
                            "allowed_categories": skill["categories"],
                            "batch_no": batch_no,
                            "failed_candidates": retryable,
                            "blocks": retry_blocks,
                        }
                        try:
                            retry_result = llm.json_call(
                                "extract_candidates_retry",
                                prompt
                                + " 上次输出含残句、孤立符号或无可核验证据的候选。只处理给定失败候选和证据Block；"
                                "每条statement必须是完整独立句，evidence_quote必须为给定Block中的连续原文，无法定位则不要输出。",
                                retry_payload,
                                **batch_call_options,
                                trace_label=f"{role}_b{batch_no:03d}",
                                trace_context={
                                    "phase": "candidate_local_retry",
                                    "document_role": role,
                                    "batch_no": batch_no,
                                    "failed_candidate_count": len(retryable),
                                },
                            )
                            retry_items = retry_result.get(result_key, [])
                            recovered, retry_rejected = validate_candidate_items(retry_items, valid_ids, blocks)
                            accepted.extend(recovered)
                            rejected = [item for item in rejected if not item.get("retryable")] + retry_rejected
                        except RuntimeError as exc:
                            cause = exc.__cause__ or exc
                            if not accepted:
                                failure = {
                                    "code": "CANDIDATE_RETRY_UNAVAILABLE",
                                    "error_type": type(cause).__name__,
                                    "message": "异常候选局部重提失败，本批未形成可用候选",
                                    "request_tokens": token_estimate(
                                        prompt + json.dumps(retry_payload, ensure_ascii=False)
                                    ),
                                }
                for item in accepted:
                    item["source_batch"] = batch_no
                write_json(
                    checkpoint,
                    {
                        "schema_version": 8,
                        "status": "completed",
                        "document_role": role,
                        "batch_no": batch_no,
                        "input_fingerprint": fingerprint,
                        "request_tokens": request_tokens,
                        "primary_block_count": batch.get("primary_block_count", len(batch.get("primary_block_ids", []))),
                        "candidate_estimate": batch.get("candidate_estimate"),
                        "table_row_count": batch.get("table_row_count"),
                        "output_characters": len(json.dumps(result, ensure_ascii=False)),
                        "accepted": accepted,
                        "rejected": rejected,
                        "failure": failure,
                    },
                )
                return batch_no, accepted, rejected, False, failure

            entries = [(int(batch.get("batch_no") or index), batch) for index, batch in enumerate(sections, start=1)]
            results = []
            with ThreadPoolExecutor(max_workers=min(workers, len(entries))) as executor:
                futures = {executor.submit(extract_batch, entry): entry[0] for entry in entries}
                for completed_count, future in enumerate(as_completed(futures), start=1):
                    batch_no = futures[future]
                    try:
                        result = future.result()
                    except Exception as exc:
                        result = (
                            batch_no, [], [], False,
                            {"code": "BATCH_EXECUTION_ERROR", "error_type": type(exc).__name__,
                             "message": "批次执行异常，本批未形成自动提取结论"},
                        )
                    results.append(result)
                    _, accepted, rejected, reused, failure = result
                    store.event(
                        "WARNING" if failure else "INFO",
                        "extract_candidates",
                        "batch_degraded" if failure else ("batch_checkpoint_reused" if reused else "batch_checkpoint_saved"),
                        f"{role}第{batch_no}批{'降级' if failure else ('复用' if reused else '保存')}检查点",
                        batch_no=batch_no,
                        completed_batches=completed_count,
                        total_batches=len(entries),
                        primary_block_count=next((batch.get("primary_block_count") for no, batch in entries if no == batch_no), None),
                        candidate_estimate=next((batch.get("candidate_estimate") for no, batch in entries if no == batch_no), None),
                        accepted_count=len(accepted),
                        rejected_count=len(rejected),
                        failure_code=failure.get("code") if failure else None,
                    )
                    if self.progress_callback:
                        progress_state = {
                            **state,
                            "step_progress": {
                                "step": "extract_candidates",
                                "completed": completed_count,
                                "total": len(entries),
                            },
                        }
                        self.progress_callback(store, progress_state, "extract_candidates")
            role_reports = []
            for batch_no, accepted, rejected, reused, failure in sorted(results, key=lambda item: item[0]):
                role_items.extend(accepted)
                role_reports.append({
                    "batch_no": batch_no,
                    "status": "degraded" if failure else "completed",
                    "accepted_count": len(accepted),
                    "rejected_count": len(rejected),
                    "reused": reused,
                    "failure": failure,
                    **{
                        key: batch.get(key)
                        for no, batch in entries if no == batch_no
                        for key in ("primary_block_count", "candidate_estimate", "table_row_count", "token_estimate")
                    },
                })
                if rejected:
                    store.event(
                        "WARNING",
                        "extract_candidates",
                        "candidates_rejected",
                        f"{role}第{batch_no}批过滤结构损坏候选",
                        count=len(rejected),
                    )
                if failure:
                    extraction_findings.append(_extraction_failure_finding(role, batch_no, failure))
            candidates[role] = role_items
            batch_reports[role] = role_reports
        return {
            "candidates": candidates,
            "status": "degraded" if extraction_findings else "completed",
            "batch_reports": batch_reports,
            "extraction_findings": extraction_findings,
        }

    def _build_ledger(
        self, store: RunStore, state: dict[str, Any], llm: LLMService, mineru: MinerUService
    ) -> dict[str, Any]:
        """对候选事项去重、标准化并生成稳定台账ID。"""
        raw = self._previous(store, "extract_candidates")["candidates"]
        parsed = self._previous(store, "parse_documents")["documents"]
        ledgers: dict[str, dict[str, Any]] = {}
        for role, items in raw.items():
            document = parsed[role]
            version_id = "DV-" + hashlib.sha256(f"{document.get('source_file')}|{document.get('document_id')}".encode()).hexdigest()[:12]
            ledgers[role] = LedgerService().build(role, items, version_id)
        return {"ledgers": ledgers, "stats": {role: {"occurrences": len(value["extraction_occurrences"]), "assertions": len(value["source_assertions"]), "clusters": len(value["business_item_clusters"])} for role, value in ledgers.items()}}

    def _build_scene_view(
        self, store: RunStore, state: dict[str, Any], llm: LLMService, mineru: MinerUService
    ) -> dict[str, Any]:
        """将采购台账投影为分类视图，供采购审查技能使用。"""
        ledgers = self._previous(store, "build_ledger")["ledgers"]
        return SceneViewService().build(ledgers.get("procurement", {}))

    def _build_compliance_matrix(
        self, store: RunStore, state: dict[str, Any], llm: LLMService, mineru: MinerUService
    ) -> dict[str, Any]:
        """按业务主题形成“法规义务—采购事实—差异”核验矩阵。"""
        view = self._previous(store, "build_scene_view")
        assertions = procurement_assertions(
            self._previous(store, "build_ledger")["ledgers"].get("procurement", {})
        )
        legal_units = self._previous(store, "match_legal_applicability").get("applicable_legal_units", [])
        rules = self._previous(store, "match_rules").get("rules", [])
        global_issues = self._previous(store, "global_validation").get("issues", [])
        workers = max(1, min(int(self.config.get("workflow", {}).get("review_workers", 2)), len(REVIEW_TOPICS)))

        def review_topic(topic: str) -> dict[str, Any]:
            topic_assertions = [
                item for item in assertions
                if item.get("category") == topic or topic in (item.get("category_tags") or [])
            ]
            topic_blocks = {
                block_id for item in topic_assertions for block_id in item.get("evidence_block_ids", [])
            }
            topic_issues = [
                issue for issue in global_issues
                if topic_blocks.intersection(issue.get("evidence_block_ids", []))
            ][:10]
            topic_rules = [
                rule for rule in rules
                if not rule.get("tags") or any(
                    str(tag) in json.dumps(topic_assertions, ensure_ascii=False)
                    for tag in rule.get("tags", [])
                )
            ][:10]
            topic_laws = rank_legal_units(
                legal_units,
                [*topic_assertions, {"statement": f"{topic} {TOPIC_FOCUS[topic]}"}],
                top_k=12,
            )
            payload = {
                "topic": topic,
                "procurement_facts": topic_assertions[:35],
                "legal_obligations": topic_laws,
                "executable_rules": topic_rules,
                "deterministic_leads": topic_issues,
            }
            try:
                result = llm.json_call(
                    "compliance_matrix",
                    "你是采购文件专项核验员。仅审查输入的一个业务主题。逐项形成法规义务/规则要求—采购事实—差异；"
                    "同时核验完整性、明确性、跨章节一致性和可执行性；即使没有法规条款也要完成业务核验。"
                    "deterministic_leads只是待核线索，不得直接当作问题。没有差异也要记录pass检查。"
                    "候选问题必须引用真实采购evidence_block_ids；法规问题还必须引用legal_unit_ids。"
                    "只返回JSON：{\"coverage_status\":\"reviewed|evidence_insufficient\",\"checks\":[{\"legal_obligation\":\"\","
                    "\"procurement_facts\":[],\"difference\":\"\",\"status\":\"pass|gap|conflict|unclear\","
                    "\"evidence_block_ids\":[],\"legal_unit_ids\":[]}],\"candidate_findings\":[{\"finding_type\":\"missing_element|ambiguity|inconsistency|reference_issue|unenforceable|legal_risk|rule_violation|evidence_insufficient\","
                    "\"risk_level\":\"high|medium|low|pending\",\"title\":\"\",\"description\":\"\",\"rationale\":\"\","
                    "\"recommendation\":\"\",\"evidence_block_ids\":[],\"evidence_quotes\":[],\"legal_unit_ids\":[],\"rule_ids\":[]}]}",
                    payload,
                )
            except Exception as exc:
                store.event("WARNING", "build_compliance_matrix", "topic_review_failed", str(exc), topic=topic)
                return {
                    "topic": topic, "coverage_status": "evidence_insufficient", "checks": [],
                    "candidate_findings": [], "error": type(exc).__name__,
                    "fact_count": len(topic_assertions), "legal_unit_count": len(topic_laws),
                }
            candidates = []
            for finding in result.get("candidate_findings", []):
                if (
                    not isinstance(finding, dict)
                    or not str(finding.get("title") or "").strip()
                    or finding.get("finding_type") in {"parse_quality", "extraction_quality"}
                ):
                    continue
                candidate = {**finding, "topic": topic}
                fingerprint = json.dumps({
                    "topic": topic, "title": candidate.get("title"),
                    "blocks": sorted(candidate.get("evidence_block_ids", [])),
                    "laws": sorted(candidate.get("legal_unit_ids", [])),
                }, ensure_ascii=False, sort_keys=True)
                candidate["candidate_id"] = "CND-" + hashlib.sha256(fingerprint.encode()).hexdigest()[:12]
                candidates.append(candidate)
            return {
                "topic": topic,
                "coverage_status": result.get("coverage_status", "reviewed"),
                "checks": result.get("checks", []),
                "candidate_findings": candidates,
                "fact_count": len(topic_assertions),
                "legal_unit_count": len(topic_laws),
            }

        audits = []
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(review_topic, topic): topic for topic in REVIEW_TOPICS}
            results = {futures[future]: future.result() for future in as_completed(futures)}
        audits = [results[topic] for topic in REVIEW_TOPICS]
        candidates = deduplicate_findings([
            finding for audit in audits for finding in audit.get("candidate_findings", [])
        ])
        return {
            "coverage_matrix": audits,
            "candidate_findings": candidates,
            "reviewed_topic_count": sum(item.get("coverage_status") == "reviewed" for item in audits),
            "candidate_count": len(candidates),
            "execution_status": "degraded" if any(item.get("error") for item in audits) else "completed",
        }

    def _agent_review(
        self, store: RunStore, state: dict[str, Any], llm: LLMService, mineru: MinerUService
    ) -> dict[str, Any]:
        """仅合并专项核验已形成证据链的业务候选问题。"""
        scenario = state["scenario"]
        rules = self._previous(store, "match_rules")
        legal_gate = self._previous(store, "match_legal_applicability")
        matrix = self._previous(store, "build_compliance_matrix")
        candidates = matrix.get("candidate_findings", [])
        candidate_index = {item["candidate_id"]: item for item in candidates if item.get("candidate_id")}
        skill = self.skills["review_agents"][scenario]
        instruction = skill["instruction"]
        if scenario == "procurement":
            instruction = self.formal_skills["procurement_review"]
        result = llm.json_call(
            "agent_review",
            instruction
            + " 当前是最终合并阶段，只能去重、合并review_candidates中已有候选，不得新增问题。"
            "每条输出必须列出source_candidate_ids；证据、规则和法规ID只能来自这些候选。"
            "解析/OCR/提取失败不属于业务问题，本阶段不得输出。"
            "返回严格JSON：{\"overall_conclusion\":\"\",\"coverage_summary\":[],\"findings\":[{"
            "\"source_candidate_ids\":[],\"finding_type\":\"\",\"risk_level\":\"high|medium|low|pending\","
            "\"title\":\"\",\"description\":\"\",\"ledger_item_ids\":[],\"evidence_block_ids\":[],"
            "\"evidence_quotes\":[],\"rule_ids\":[],\"legal_unit_ids\":[],\"legal_applicability\":\"not_assessed|applicable\","
            "\"rationale\":\"\",\"recommendation\":\"\",\"confidence\":0.0,\"needs_human_confirmation\":true}]}。",
            {
                "scenario": scenario,
                "coverage_matrix": matrix.get("coverage_matrix", []),
                "review_candidates": candidates,
                "rule_coverage": {"matched_count": rules.get("matched_count", 0), "rule_source": rules.get("rule_source")},
                "legal_context": {
                    "mode": legal_gate.get("mode", "applicability_gate"),
                    "decisions": legal_gate.get("decisions", []),
                },
            },
        )
        findings = []
        for finding in result.get("findings", []):
            source_ids = [value for value in finding.get("source_candidate_ids", []) if value in candidate_index]
            if not source_ids:
                continue
            sources = [candidate_index[value] for value in source_ids]
            allowed_blocks = {value for item in sources for value in item.get("evidence_block_ids", [])}
            allowed_laws = {value for item in sources for value in item.get("legal_unit_ids", [])}
            allowed_rules = {value for item in sources for value in item.get("rule_ids", [])}
            requested_blocks = [value for value in finding.get("evidence_block_ids", []) if value in allowed_blocks]
            requested_laws = [value for value in finding.get("legal_unit_ids", []) if value in allowed_laws]
            requested_rules = [value for value in finding.get("rule_ids", []) if value in allowed_rules]
            findings.append({
                **finding,
                "source_candidate_ids": source_ids,
                "evidence_block_ids": requested_blocks or sorted(allowed_blocks),
                "legal_unit_ids": requested_laws or sorted(allowed_laws),
                "rule_ids": requested_rules or sorted(allowed_rules),
                "legal_applicability": "applicable" if allowed_laws else "not_assessed",
            })
        return {
            "agent": skill["name"],
            "overall_conclusion": str(result.get("overall_conclusion", "AI初审候选问题已完成证据链合并。")),
            "coverage_summary": [{
                "topic": item["topic"], "status": item.get("coverage_status", "evidence_insufficient"),
                "fact_count": item.get("fact_count", 0), "legal_unit_count": item.get("legal_unit_count", 0),
            } for item in matrix.get("coverage_matrix", [])],
            "coverage_matrix": matrix.get("coverage_matrix", []),
            "findings": deduplicate_findings(findings),
            "tool_results": {"compliance_matrix": matrix},
        }

    def _global_validation(
        self, store: RunStore, state: dict[str, Any], llm: LLMService, mineru: MinerUService
    ) -> dict[str, Any]:
        """执行不依赖LLM的缺项、引用和关键时间冲突检查。"""
        parsed = self._previous(store, "parse_documents")
        view = self._previous(store, "build_scene_view")
        issues: list[dict[str, Any]] = []
        resolved_references: list[dict[str, Any]] = []
        if state["scenario"] == "procurement":
            required = {"资格与实质性条件", "技术需求与验收", "评审办法与评分", "商务报价与付款", "合同履约与责任"}
            for category in sorted(required - set(view.get("topic_views", {}))):
                issues.append(
                    {
                        "code": "POSSIBLE_MISSING_CATEGORY",
                        "message": f"未提取到“{category}”事项",
                        "evidence_block_ids": [],
                        "needs_human_confirmation": True,
                    }
                )
        for role, document in parsed["documents"].items():
            blocks = document.get("blocks", [])
            events: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
            for block in blocks:
                text = str(block.get("text") or "")
                for event_name in ("投标截止时间", "响应截止时间", "开标时间"):
                    if event_name in text:
                        for date in re.findall(r"20\d{2}[年./-]\d{1,2}[月./-]\d{1,2}日?", text):
                            events[event_name][date].append(block["block_id"])
            for reference in deterministic_references(blocks):
                resolved_references.append({"document_role": role, **reference})
                if reference["status"] in {"unresolved", "ambiguous"}:
                    issues.append({
                        "code": "UNRESOLVED_REFERENCE" if reference["status"] == "unresolved" else "AMBIGUOUS_REFERENCE",
                        "message": f"{role}中的引用“{reference['reference_text']}”{('未找到目标' if reference['status'] == 'unresolved' else '存在多个候选目标')}",
                        "evidence_block_ids": reference["source_block_ids"],
                        "needs_human_confirmation": True,
                    })
            for event_name, dates in events.items():
                if len(dates) > 1:
                    issues.append(
                        {
                            "code": "POSSIBLE_DATE_CONFLICT",
                            "message": f"{role}中的{event_name}出现多个日期：{sorted(dates)}",
                            "evidence_block_ids": sorted({bid for ids in dates.values() for bid in ids}),
                            "needs_human_confirmation": True,
                        }
                    )
        return {"issues": deduplicate_objects(issues), "issue_count": len(deduplicate_objects(issues)), "resolved_references": resolved_references}

    def _match_rules(
        self, store: RunStore, state: dict[str, Any], llm: LLMService, mineru: MinerUService
    ) -> dict[str, Any]:
        """筛选可执行规则，并从法规知识目录召回相关条款单元。"""
        config = self.config.get("rules", {})
        rules = RuleRepository(Path(get_settings().data_dir)).applicable_rules(state["scenario"])
        rule_source = "JsonRuleRepository"
        view_text = json.dumps(self._previous(store, "build_scene_view"), ensure_ascii=False)
        matched = search_rules(
            context=ToolContext(run_id=str(getattr(store, "run_id", state.get("run_id", "workflow")))),
            rules=rules,
            scenario=state["scenario"],
            text=view_text,
            top_k=max(1, len(rules)),
        )["rules"]

        legal_documents: list[dict[str, Any]] = []
        legal_sources: list[dict[str, Any]] = []
        legal_source_stats = {"included": 0, "excluded_unknown": 0, "excluded_repealed": 0, "excluded_unconfirmed_profile": 0, "excluded_other": 0}
        knowledge_root = config.get("knowledge_root")
        if knowledge_root and state["scenario"] == "procurement":
            root = Path(str(knowledge_root)).expanduser().resolve()
            for knowledge_path in sorted(root.glob("*/legal_knowledge.json")) if root.is_dir() else []:
                knowledge = read_json(knowledge_path)
                document = knowledge.get("legal_document", {})
                quality = knowledge.get("quality", {})
                status = document.get("status") or "unknown"
                extraction = knowledge.get("metadata_extraction", {})
                profile = document.get("applicability") or {}
                profile_confirmed = extraction.get("status") == "confirmed" and isinstance(profile, dict)
                document_key = str(document.get("document_key") or knowledge_path.parent.name)
                fallbacks = []
                if not document.get("metadata_version"):
                    fallbacks.append("metadata_version defaulted to 1 because the source document has no version")
                source_identifier = str(document.get("source_storage_key") or document.get("source_file") or document_key)
                if not document.get("source_storage_key") and not document.get("source_file"):
                    fallbacks.append("source fingerprint uses document_key because no source identifier is stored")
                freeze = {
                    "document_key": document_key,
                    "metadata_version": int(document.get("metadata_version") or 1),
                    "source_fingerprint": "sha256:" + hashlib.sha256(source_identifier.encode("utf-8")).hexdigest(),
                    "content_fingerprint": "sha256:" + hashlib.sha256(knowledge_path.read_bytes()).hexdigest(),
                    "fallbacks": fallbacks,
                }
                applicability_enabled = self.config.get("legal_applicability", {}).get("enabled", False)
                included = status == "effective" and (profile_confirmed or not applicability_enabled)
                source = {
                    "document_key": document_key,
                    "title": document.get("title"),
                    "status": status,
                    "effective_date": document.get("effective_date"),
                    "quality_status": quality.get("status"),
                    "profile_status": extraction.get("status"),
                    "included": included,
                    "source_freeze": freeze,
                }
                legal_sources.append(source)
                if included:
                    legal_source_stats["included"] += 1
                    legal_documents.append({"source": source, "applicability": profile, "units": knowledge.get("units", [])})
                elif status == "unknown":
                    legal_source_stats["excluded_unknown"] += 1
                elif status == "repealed":
                    legal_source_stats["excluded_repealed"] += 1
                elif status == "effective":
                    legal_source_stats["excluded_unconfirmed_profile"] += 1
                else:
                    legal_source_stats["excluded_other"] += 1
        warnings = []
        if not rules:
            warnings.append("未配置可执行规则库")
        if not legal_documents:
            warnings.append("未召回法规条款")
        if any(source.get("quality_status") != "reviewable" for source in legal_sources if source["included"]):
            warnings.append(
                "部分法规知识解析质量未标记为reviewable，引用具体条文时仍需证据校验"
                if not applicability_enabled
                else "部分法规效力元数据未确认，只能作为候选依据"
            )
        excluded_count = sum(count for name, count in legal_source_stats.items() if name != "included")
        if excluded_count:
            warnings.append(f"已排除 {excluded_count} 份未生效或已失效法规文档")
        for warning in warnings:
            store.event("WARNING", "match_rules", "rule_warning", warning)
        degraded_reasons = []
        if not rules:
            degraded_reasons.append("executable_rule_library_empty")
        if not legal_documents:
            degraded_reasons.append("legal_context_empty")
        return {
            "rules": matched,
            "matched_count": len(matched),
            "rule_source": rule_source,
            "execution_status": "degraded" if degraded_reasons else "completed",
            "degraded_reasons": degraded_reasons,
            "legal_sources": legal_sources,
            "legal_source_stats": legal_source_stats,
            "legal_documents": legal_documents,
            "warnings": warnings,
        }

    def _derive_legal_facts(
        self, store: RunStore, state: dict[str, Any], llm: LLMService, mineru: MinerUService
    ) -> dict[str, Any]:
        if not self.config.get("legal_applicability", {}).get("enabled", False):
            store.event("WARNING", "derive_legal_facts", "step_degraded", "法规适用性未启用，跳过任务法律事实推导")
            return {
                "method": "disabled",
                "execution_status": "disabled",
                "degraded_reasons": ["legal_applicability_disabled"],
                "task_legal_facts": {},
                "missing_facts": [],
            }
        parsed = self._previous(store, "parse_documents")
        ledger = procurement_assertions(self._previous(store, "build_ledger")["ledgers"].get("procurement", {}))
        documents = parsed.get("documents", {})
        task_context = state.get("task_context", {})
        facts = derive_task_legal_facts(documents, ledger, task_context)
        unknown = [name for name in LEGAL_FACT_FIELDS if facts.get(name) == "unknown"]
        if unknown:
            store.event("WARNING", "derive_legal_facts", "facts_incomplete", "legal applicability will not infer unknown facts", missing_facts=unknown)
        return {
            "method": "deterministic_rules",
            "execution_status": "completed",
            "degraded_reasons": [],
            "input": {
                "task_context": task_context,
                "document_blocks": [
                    {
                        "document_role": role,
                        "block_id": block.get("block_id"),
                        "page_no": block.get("page_no"),
                        "heading_path": block.get("heading_path", []),
                        "text": block.get("text"),
                    }
                    for role, document in documents.items()
                    for block in document.get("blocks", [])
                    if block.get("text")
                ],
                "ledger_assertions": ledger,
            },
            "task_legal_facts": facts,
            "missing_facts": unknown,
        }

    def _match_legal_applicability(
        self, store: RunStore, state: dict[str, Any], llm: LLMService, mineru: MinerUService
    ) -> dict[str, Any]:
        matched = self._previous(store, "match_rules")
        if not self.config.get("legal_applicability", {}).get("enabled", False):
            documents = matched.get("legal_documents", [])
            procurement_items = procurement_assertions(self._previous(store, "build_ledger")["ledgers"].get("procurement", {}))
            units = [
                {**unit, "document_key": document["source"]["document_key"]}
                for document in documents
                for unit in document.get("units", [])
            ]
            ranked_units = rank_legal_units(units, procurement_items, top_k=120)
            return {
                "mode": "all_eligible_laws",
                "execution_status": "degraded",
                "degraded_reasons": ["legal_applicability_disabled"],
                "task_legal_facts": {},
                "decisions": [
                    {
                        "document_key": document["source"]["document_key"],
                        "title": document["source"].get("title"),
                        "status": "applicable",
                        "reasons": ["适用性匹配功能当前未启用，按全量法规上下文处理"],
                        "evidence": {},
                        "missing_facts": [],
                        "source_freeze": document["source"]["source_freeze"],
                    }
                    for document in documents
                ],
                "applicable_legal_units": ranked_units,
                "candidate_legal_units": ranked_units,
                "frozen_context": [document["source"]["source_freeze"] for document in documents],
                "candidate_frozen_context": [document["source"]["source_freeze"] for document in documents],
                "warnings": list(matched.get("warnings", [])),
            }
        facts = self._previous(store, "derive_legal_facts")["task_legal_facts"]
        decisions = match_legal_documents(facts, matched.get("legal_documents", []))
        applicable = [item for item in decisions if item["status"] == "applicable"]
        procurement_items = procurement_assertions(self._previous(store, "build_ledger")["ledgers"].get("procurement", {}))
        candidate_units = [{**unit, "document_key": item["document_key"]} for item in decisions for unit in item.get("_units", [])]
        units = [{**unit, "document_key": item["document_key"]} for item in applicable for unit in item.pop("_units")]
        ranked_units = rank_legal_units(units, procurement_items, top_k=30)
        ranked_candidates = rank_legal_units(candidate_units, procurement_items, top_k=120)
        for item in decisions:
            item.pop("_units", None)
        warnings = list(matched.get("warnings", []))
        if not applicable:
            warnings.append("no legal document entered the formal legal context; legal conclusions require human confirmation")
            store.event("WARNING", "match_legal_applicability", "legal_context_empty", warnings[-1])
        return {
            "mode": "applicability_gate",
            "execution_status": "completed" if applicable else "degraded",
            "degraded_reasons": [] if applicable else ["formal_legal_context_empty"],
            "task_legal_facts": facts,
            "decisions": decisions,
            "applicable_legal_units": ranked_units,
            "candidate_legal_units": ranked_candidates,
            "frozen_context": [item["source_freeze"] for item in applicable],
            "candidate_frozen_context": [item["source_freeze"] for item in decisions],
            "warnings": warnings,
        }

    def _validate_evidence(
        self, store: RunStore, state: dict[str, Any], llm: LLMService, mineru: MinerUService
    ) -> dict[str, Any]:
        """过滤不存在的Block证据，无证据问题降级为待人工确认。"""
        parsed = self._previous(store, "parse_documents")
        review = self._previous(store, "agent_review")
        global_validation = self._previous(store, "global_validation")
        legal_gate = self._previous(store, "match_legal_applicability")
        block_index = {
            b["block_id"]: {
                "document_role": role,
                "page_no": b.get("page_no"),
                "bbox": b.get("bbox"),
                "quote": str(b.get("text") or ""),
                "full_text": str(b.get("text") or ""),
                "heading_path": b.get("heading_path", []),
            }
            for role, document in parsed["documents"].items()
            for b in document.get("blocks", [])
        }
        legal_index = {
            unit["legal_unit_id"]: unit
            for unit in legal_gate.get("applicable_legal_units", [])
            if unit.get("legal_unit_id")
        }
        findings = []
        rule_index = {str(rule.get("id")): rule for rule in self._previous(store, "match_rules").get("rules", []) if rule.get("id")}
        validator = EvidenceValidationService()
        for index, finding in enumerate(review.get("findings", []), start=1):
            finding_for_validation = {**finding}
            if finding.get("finding_type") == "missing_element":
                title = str(finding.get("title") or "")
                finding_for_validation["absence_check_verified"] = any(
                    issue.get("code") == "POSSIBLE_MISSING_CATEGORY"
                    and (match := re.search(r"“(.+?)”", str(issue.get("message") or ""))) is not None
                    and match.group(1) in title
                    for issue in global_validation.get("issues", [])
                )
            result = validator.validate(finding_for_validation, block_index, legal_index, rule_index)
            ids = result["valid_block_ids"]
            evidence = result["evidence"]
            requested_legal_ids = [str(value) for value in finding.get("legal_unit_ids", [])]
            legal_ids = [unit_id for unit_id in requested_legal_ids if unit_id in legal_index]
            legal_evidence = [{
                "legal_unit_id": unit_id,
                "document_title": legal_index[unit_id].get("document_title"),
                "article_no": legal_index[unit_id].get("article_no"),
                "paragraph_no": legal_index[unit_id].get("paragraph_no"),
                "item_no": legal_index[unit_id].get("item_no"),
                "text": legal_index[unit_id].get("text"),
                "status": legal_index[unit_id].get("status"),
                "evidence": legal_index[unit_id].get("evidence", []),
            } for unit_id in legal_ids]
            validated = {
                "finding_id": f"F-{index:04d}",
                **finding,
                "evidence_block_ids": ids,
                "evidence": evidence,
                "evidence_status": result["evidence_status"],
                "evidence_validation": {key: value for key, value in result.items() if key != "evidence"},
                "legal_unit_ids": legal_ids,
                "legal_evidence": legal_evidence,
                "legal_evidence_status": "verified" if legal_evidence else "not_cited",
            }
            if result["evidence_status"] != "verified":
                validated["needs_human_confirmation"] = True
            if finding.get("finding_type") == "legal_risk" and not legal_evidence:
                validated["needs_human_confirmation"] = True
                validated["legal_evidence_status"] = "insufficient"
            findings.append(validated)
        return {
            "agent": review["agent"],
            "overall_conclusion": review["overall_conclusion"],
            "coverage_summary": review.get("coverage_summary", []),
            "coverage_matrix": review.get("coverage_matrix", []),
            "findings": findings,
            "verified_count": sum(f["evidence_status"] == "verified" for f in findings),
            "insufficient_count": sum(f["evidence_status"] != "verified" for f in findings),
        }

    def _final_report(
        self, store: RunStore, state: dict[str, Any], llm: LLMService, mineru: MinerUService
    ) -> dict[str, Any]:
        """汇总本次MVP结果，不替代经办和监督的最终确认。"""
        evidence = self._previous(store, "validate_evidence")
        ledger = self._previous(store, "build_ledger")
        view = self._previous(store, "build_scene_view")
        legal_gate = self._previous(store, "match_legal_applicability")
        rules = self._previous(store, "match_rules")
        try:
            compliance = self._previous(store, "build_compliance_matrix")
        except (FileNotFoundError, KeyError):
            compliance = {"coverage_matrix": [], "execution_status": "completed"}
        try:
            extraction = self._previous(store, "extract_candidates")
        except (FileNotFoundError, KeyError):
            extraction = {}
        try:
            quality = self._previous(store, "quality_check")
        except (FileNotFoundError, KeyError):
            quality = {}
        degraded_steps = []
        quality_reports = quality.get("quality", {})
        degraded_quality = [report for report in quality_reports.values() if report.get("status") == "degraded"]
        if degraded_quality:
            degraded_steps.append({
                "step": "quality_check",
                "reasons": sorted({
                    issue.get("code") for report in degraded_quality for issue in report.get("issues", [])
                    if issue.get("severity") == "warning"
                }),
            })
        if extraction.get("status") == "degraded":
            degraded_steps.append({
                "step": "extract_candidates",
                "reasons": sorted({
                    report.get("failure", {}).get("code")
                    for reports in extraction.get("batch_reports", {}).values()
                    for report in reports if report.get("failure")
                }),
            })
        if rules.get("execution_status") == "degraded":
            degraded_steps.append({"step": "match_rules", "reasons": rules.get("degraded_reasons", [])})
        if legal_gate.get("execution_status") == "degraded":
            degraded_steps.append({"step": "match_legal_applicability", "reasons": legal_gate.get("degraded_reasons", [])})
        if compliance.get("execution_status") == "degraded":
            degraded_steps.append({"step": "build_compliance_matrix", "reasons": ["topic_review_failed"]})
        if evidence.get("insufficient_count"):
            degraded_steps.append({"step": "validate_evidence", "reasons": ["evidence_insufficient"]})
        topic_count = len(view.get("topic_views", {}))
        overall_conclusion = (
            f"AI初审完成：采购台账覆盖{topic_count}类主题，形成{len(evidence['findings'])}项候选问题；"
            f"证据校验通过{evidence['verified_count']}项、待人工确认{evidence['insufficient_count']}项。"
            + ("规则或法规能力处于降级模式，相关结论不得视为完整合规判断。" if degraded_steps else "")
            + "所有结论均需人工复核。"
        )
        return {
            "run_id": state["run_id"],
            "scenario": state["scenario"],
            "overall_conclusion": overall_conclusion,
            "agent_overall_conclusion": evidence["overall_conclusion"],
            "pipeline_status": "degraded" if degraded_steps else "completed",
            "degraded_steps": degraded_steps,
            "finding_count": len(evidence["findings"]),
            "verified_finding_count": evidence["verified_count"],
            "evidence_insufficient_count": evidence["insufficient_count"],
            "ledger_stats": ledger["stats"],
            "scene_view": view,
            "coverage_matrix": evidence.get("coverage_matrix", compliance.get("coverage_matrix", [])),
            "legal_basis_summary": {
                "task_legal_facts": legal_gate["task_legal_facts"],
                "decisions": legal_gate["decisions"],
                "frozen_context": legal_gate["frozen_context"],
                "warnings": legal_gate["warnings"],
            },
            "task_legal_facts": legal_gate["task_legal_facts"],
            "legal_applicability": legal_gate["decisions"],
            "legal_context_freeze": legal_gate["frozen_context"],
            "findings": evidence["findings"],
            "system_warnings": collect_system_warnings(quality, extraction),
            "human_review_required": True,
        }


def validate_request(scenario: str, documents: dict[str, str], pause_after: str | None) -> None:
    """校验场景、文件角色、运行模式和断点名称。"""
    if scenario not in REQUIRED_ROLES:
        raise ValueError(f"未知场景：{scenario}")
    missing = REQUIRED_ROLES[scenario] - set(documents)
    if missing:
        raise ValueError(f"{scenario}场景缺少文件角色：{sorted(missing)}")
    for role, path in documents.items():
        if role not in {"procurement", "response", "contract"}:
            raise ValueError(f"未知文档角色：{role}")
        if not Path(path).expanduser().is_file():
            raise FileNotFoundError(f"输入文件不存在：{path}")
    if pause_after is not None and pause_after not in STEPS:
        raise ValueError(f"未知断点：{pause_after}")


def load_formal_skill(skill_dir: Path, include_references: bool = True) -> str:
    """加载正式SKILL.md及其直接references，供项目运行时调用。"""
    skill_path = skill_dir / "SKILL.md"
    if not skill_path.is_file():
        raise FileNotFoundError(f"正式Skill不存在：{skill_path}")
    parts = [skill_path.read_text(encoding="utf-8")]
    references = skill_dir / "references"
    if include_references and references.is_dir():
        for path in sorted(references.glob("*.md")):
            parts.append(f"\n# 引用：{path.name}\n{path.read_text(encoding='utf-8')}")
    return "\n".join(parts)


def merge_structure_profiles(
    base: dict[str, Any], partials: list[dict[str, Any]], quality: dict[str, Any]
) -> dict[str, Any]:
    """合并章节级结构结果，同时以确定性完整目录覆盖模型遗漏。"""
    profile = {
        "skill": "understand-document-structure",
        "skill_version": "1.0.0",
        **base,
        "quality_status": quality.get("status"),
        "section_responsibilities": list(base.get("section_responsibilities", [])),
        "parties": [],
        "terms": [],
        "references": list(base.get("references", [])),
        "clause_relations": list(base.get("clause_relations", [])),
        "global_constraints": [],
        "inventories": {
            name: list(base.get("inventories", {}).get(name, []))
            for name in ("tables", "images", "attachments")
        },
        "warnings": list(quality.get("issues", [])),
        "unresolved": [],
    }
    for key in ("section_responsibilities", "parties", "terms", "references", "clause_relations", "global_constraints", "warnings", "unresolved"):
        values = [item for partial in partials for item in partial.get(key, []) if isinstance(item, dict)]
        profile[key] = deduplicate_objects(profile.get(key, []) + values)
    for inventory in ("tables", "images", "attachments"):
        values = [
            item
            for partial in partials
            for item in partial.get("inventories", {}).get(inventory, [])
            if isinstance(item, dict)
        ]
        profile["inventories"][inventory] = deduplicate_objects(profile["inventories"][inventory] + values)
    return profile


def deduplicate_objects(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按规范化JSON去重章节批次合并结果。"""
    unique: dict[str, dict[str, Any]] = {}
    for item in items:
        unique[json.dumps(item, ensure_ascii=False, sort_keys=True)] = item
    return list(unique.values())


def summarize(value: Any) -> dict[str, Any]:
    """生成日志用的小摘要，避免把全文写进events.jsonl。"""
    if not isinstance(value, dict):
        return {"type": type(value).__name__}
    summary: dict[str, Any] = {"keys": list(value)[:12]}
    for key in ("documents", "profiles", "candidates", "ledgers", "quality"):
        if isinstance(value.get(key), dict):
            summary[f"{key}_counts"] = {
                name: len(item) if isinstance(item, (list, dict)) else 1 for name, item in value[key].items()
            }
    for key in ("finding_count", "verified_count", "insufficient_count"):
        if key in value:
            summary[key] = value[key]
    return summary


def block_payload(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """构造结构理解输入Block；版面噪声保留在原文层但不发送给LLM。"""
    return [
        {
            "block_id": b.get("block_id"),
            "block_type": b.get("block_type"),
            "heading_path": b.get("heading_path", []),
            "page_no": b.get("page_no"),
            "text": b.get("text", ""),
        }
        for b in blocks
        if b.get("text") and b.get("block_type") not in {"header", "footer", "page_number"}
    ]


def structure_context_for_blocks(
    profile: dict[str, Any], block_ids: set[str], include_all: bool = False
) -> dict[str, Any]:
    """Project verified structure facts into a batch; summaries never become evidence."""
    def ids(item: dict[str, Any]) -> set[str]:
        values: set[str] = set()
        for key in ("block_id", "block_ids", "evidence_block_ids", "source_block_ids", "target_block_ids"):
            value = item.get(key)
            if isinstance(value, list):
                values.update(str(part) for part in value if part)
            elif value:
                values.add(str(value))
        return values

    def relevant(items: Any, limit: int = 100) -> list[dict[str, Any]]:
        if not isinstance(items, list):
            return []
        return [
            item for item in items
            if isinstance(item, dict) and (include_all or bool(ids(item) & block_ids))
        ][:limit]

    if not include_all:
        return {
            key: values
            for key, values in {
                "terms": relevant(profile.get("terms")),
                "references": relevant(profile.get("references")),
                "global_constraints": relevant(profile.get("global_constraints")),
                "unresolved": relevant(profile.get("unresolved")),
            }.items()
            if values
        }

    inventories = profile.get("inventories", {}) if isinstance(profile.get("inventories"), dict) else {}
    return {
        "quality_status": profile.get("quality_status"),
        "section_responsibilities": relevant(profile.get("section_responsibilities")),
        "terms": relevant(profile.get("terms")),
        "references": relevant(profile.get("references")),
        "global_constraints": relevant(profile.get("global_constraints")),
        "attachments": relevant(inventories.get("attachments")),
        "unresolved": relevant(profile.get("unresolved")),
        "evidence_policy": "结构画像只用于语义上下文，任何候选问题仍须引用原始Block ID。",
}


def compact_batch_quality(validation: Any) -> dict[str, Any] | None:
    """Do not repeat the full batch-validation report in every model request."""
    if not isinstance(validation, dict):
        return None
    codes = [
        issue.get("code")
        for issue in validation.get("issues", [])
        if isinstance(issue, dict) and issue.get("code")
    ]
    return {"status": validation.get("status"), "issue_codes": list(dict.fromkeys(codes))}


def extraction_batch_payload(role: str, blocks: list[dict[str, Any]]) -> dict[str, Any]:
    """构造事项提取的精简输入，章节信息每批只发送一次。"""
    paths: list[list[str]] = []
    hints: list[str] = []
    for block in blocks:
        path = [str(part) for part in block.get("heading_path", []) if part]
        if path and path not in paths:
            paths.append(path)
        if block.get("block_type") == "heading" and block.get("text"):
            hint = classify_structure_heading(role, str(block["text"]))
            if hint and hint not in hints:
                hints.append(hint)
    return {
        "section_context": {"paths": paths, "category_hints": hints},
        "blocks": [
            {
                "id": block.get("block_id"),
                "t": block.get("block_type"),
                "p": block.get("page_no"),
                "r": block.get("role"),
                "x": block.get("text", ""),
                **({"tf": block.get("table_fragment")} if block.get("table_fragment") else {}),
            }
            for block in blocks
            if block.get("text") and block.get("block_type") not in {"header", "footer", "page_number"}
        ],
    }


STRUCTURE_HEADING_RULES = {
    "procurement": [
        (r"公告|邀请|项目概况", "采购公告与项目概况"),
        (r"须知|前附表", "供应商须知"),
        (r"资格|资质|实质性", "资格与实质性条件"),
        (r"技术|需求|参数|验收", "技术需求与验收"),
        (r"评审|评分|评标", "评审办法与评分"),
        (r"报价|商务|付款|结算", "商务报价与付款"),
        (r"合同|履约|违约", "合同范本与履约"),
        (r"附件|格式|响应文件", "附件与格式文件"),
        (r"目录", "目录"),
    ],
    "response": [
        (r"资格|资质|证明", "资格响应"),
        (r"技术|参数|验收", "技术响应"),
        (r"商务|报价|价格", "商务与报价响应"),
        (r"偏离|承诺", "偏离与承诺"),
        (r"附件|目录", "附件与目录"),
    ],
    "contract": [
        (r"主体|甲方|乙方|当事人", "合同主体"),
        (r"标的|范围|内容", "合同标的与范围"),
        (r"金额|价款|税率", "金额与税率"),
        (r"交付|履约|验收|质保", "履约与验收"),
        (r"付款|结算", "付款结算"),
        (r"违约|责任|争议", "责任与争议"),
        (r"保密|知识产权", "保密与知识产权"),
        (r"附件|目录", "附件与目录"),
    ],
}


def classify_structure_heading(role: str, title: str) -> str | None:
    """按文档角色把明确标题映射为章节职责；无法判断时返回None。"""
    return next((label for pattern, label in STRUCTURE_HEADING_RULES[role] if re.search(pattern, title)), None)


def structure_review_batches(
    blocks: list[dict[str, Any]], role: str, max_chars: int
) -> list[list[dict[str, Any]]]:
    """仅选择无标题文档或整批没有可分类标题的章节批次交给LLM。"""
    batches = section_batches(blocks, max_chars)
    headings = [b for b in blocks if b.get("block_type") == "heading" and b.get("text")]
    if not headings:
        return batches
    return [
        batch
        for batch in batches
        if not any(
            b.get("block_type") == "heading"
            and b.get("text")
            and classify_structure_heading(role, str(b["text"]))
            for b in batch
        )
    ]


def deterministic_inventories(blocks: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """从Block类型和附件标题生成无需LLM的表格、图片与附件清单。"""
    def item(block: dict[str, Any]) -> dict[str, Any]:
        return {
            "block_id": block.get("block_id"),
            "page_no": block.get("page_no"),
            "title": str(block.get("text") or "")[:200],
        }

    return {
        "tables": [item(b) for b in blocks if b.get("block_type") == "table"],
        "images": [item(b) for b in blocks if b.get("block_type") == "image"],
        "attachments": [
            item(b)
            for b in blocks
            if b.get("block_type") == "heading" and re.search(r"附件|附录|格式", str(b.get("text") or ""))
        ],
    }


def deterministic_clause_relations(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Identify article/paragraph/item parentage while retaining original Block IDs."""
    relations, current_article, current_item = [], None, None
    for block in blocks:
        text = str(block.get("text") or "").strip()
        if re.match(r"^第[一二三四五六七八九十百千万\d]+条", text):
            current_article, current_item = block.get("block_id"), None
            continue
        if re.match(r"^[（(][一二三四五六七八九十\d]+[）)]", text):
            if current_article:
                relations.append({"parent_block_id": current_article, "child_block_id": block.get("block_id"), "relation_type": "article_item"})
            current_item = block.get("block_id")
            continue
        if re.match(r"^\d+[.、]", text) and (current_item or current_article):
            relations.append({"parent_block_id": current_item or current_article, "child_block_id": block.get("block_id"), "relation_type": "item_subitem"})
    return relations


def deterministic_references(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Resolve explicit attachment, chapter, fore-table and numbered-clause references globally."""
    targets: dict[str, list[str]] = defaultdict(list)
    reference_pattern = re.compile(
        r"附件\s*[一二三四五六七八九十\d]+|"
        r"第[一二三四五六七八九十\d]+章(?:第?\s*\d+(?:\.\d+)*\s*款)?|"
        r"前附表|第\s*\d+(?:\.\d+)*\s*款"
    )
    def keys(text: str) -> set[str]:
        return {re.sub(r"\s+", "", value) for value in reference_pattern.findall(text)}

    for block in blocks:
        text = plain_evidence_text(block.get("text"))
        if block.get("block_type") == "heading" or "前附表" in text or re.match(r"^\s*\d+(?:\.\d+)+", text):
            for key in keys(text):
                targets[key].append(block.get("block_id"))
            number = re.match(r"^\s*(\d+(?:\.\d+)+)", text)
            if number:
                targets[f"第{number.group(1)}款"].append(block.get("block_id"))
    references = []
    for block in blocks:
        text = plain_evidence_text(block.get("text"))
        if not re.search(r"详见|参见|见第|见前附表|依据|按照", text):
            continue
        for value in reference_pattern.findall(text):
            key = re.sub(r"\s+", "", value)
            if block.get("block_id") in targets.get(key, []):
                continue
            matched = list(dict.fromkeys(targets.get(key, [])))
            if not matched and "章" in key:
                chapter, clause = key.split("章", 1)
                matched = list(dict.fromkeys(targets.get(clause, []) or targets.get(chapter + "章", [])))
            references.append({"reference_text": value, "source_block_ids": [block.get("block_id")], "target_block_ids": matched, "relation_type": "attachment_reference", "status": "resolved" if len(matched) == 1 else "ambiguous" if matched else "unresolved", "confidence": 1.0 if len(matched) == 1 else 0.5})
    return references


def section_batches(blocks: list[dict[str, Any]], max_chars: int) -> list[list[dict[str, Any]]]:
    """按标题优先、字符上限兜底生成运行时章节批次，不持久化固定Chunk。"""
    batches: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    size = 0
    for block in blocks:
        text = str(block.get("text") or "")
        text_size = len(text)
        if text_size > max_chars:
            if current:
                batches.append(current)
                current, size = [], 0
            for fragment_index, start in enumerate(range(0, text_size, max_chars), start=1):
                fragment = {
                    **block,
                    "text": text[start : start + max_chars],
                    "runtime_fragment": {
                        "index": fragment_index,
                        "char_range": [start, min(start + max_chars, text_size)],
                    },
                }
                batches.append([fragment])
            continue
        starts_section = block.get("block_type") == "heading" and current
        if current and (size + text_size > max_chars or starts_section and size > max_chars // 3):
            batches.append(current)
            current, size = [], 0
        current.append(block)
        size += text_size
    if current:
        batches.append(current)
    return batches or [[]]


def batch_manifest(batch_no: int, blocks: list[dict[str, Any]]) -> dict[str, Any]:
    """Persist a readable record of a runtime batch without duplicating full text."""
    headings = [str(block.get("text") or "") for block in blocks if block.get("block_type") == "heading"]
    return {
        "batch_no": batch_no,
        "block_count": len(blocks),
        "character_count": sum(len(str(block.get("text") or "")) for block in blocks),
        "page_range": [min((block.get("page_no") or 0 for block in blocks), default=0), max((block.get("page_no") or 0 for block in blocks), default=0)],
        "heading": headings[0] if headings else "无独立标题的内容单元",
        "block_ids": [block.get("block_id") for block in blocks],
    }


def derive_candidate_hints(role: str, blocks: list[dict[str, Any]], categories: list[str]) -> list[dict[str, Any]]:
    """Derive candidate hints from locally parsed blocks."""
    keywords = {
        "procurement": "应|须|不得|资格|评分|报价|限价|验收|付款|合同|期限|时间",
        "response": "响应|承诺|满足|提供|偏离|报价|资质|业绩|人员|参数",
        "contract": "甲方|乙方|应|合同|金额|付款|交付|验收|质保|违约|期限",
    }[role]
    items = []
    for block in blocks:
        text = str(block.get("text") or "").strip()
        if not text or not re.search(keywords, text):
            continue
        category = classify_candidate_category(role, text, categories)
        items.append(
            {
                "category": category,
                "statement": text[:800],
                "subject": "",
                "action": "",
                "condition": "",
                "value": "",
                "mandatory": bool(re.search(r"应|须|不得|必须", text)),
                "evidence_block_ids": [block["block_id"]],
                "evidence_quote": text[:300],
            }
        )
    return items


def classify_candidate_category(role: str, text: str, categories: list[str]) -> str:
    """Classify a locally derived candidate hint by keyword."""
    maps = {
        "procurement": [
            ("资格|资质|业绩", "资格与实质性条件"),
            ("评分|分值|评审", "评审办法与评分"),
            ("技术|参数|验收", "技术需求与验收"),
            ("报价|限价|付款|结算", "商务报价与付款"),
            ("合同|违约|履约|质保", "合同履约与责任"),
            ("附件|格式|引用", "附件与引用"),
        ],
        "response": [
            ("资格|资质|证书|业绩", "资格响应"),
            ("技术|参数|验收", "技术响应"),
            ("报价|价格", "报价"),
            ("偏离", "偏离"),
            ("承诺|附件", "承诺与附件"),
        ],
        "contract": [
            ("主体|甲方|乙方", "合同主体"),
            ("金额|税率", "金额与税率"),
            ("交付|验收", "交付与验收"),
            ("付款|结算", "付款结算"),
            ("质保|服务", "质保服务"),
            ("违约", "违约责任"),
            ("保密|知识产权", "保密与知识产权"),
        ],
    }
    for pattern, category in maps[role]:
        if re.search(pattern, text):
            return category
    return categories[0]


def validate_candidate_items(
    items: Any, valid_ids: set[str], blocks: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Only admit complete, source-grounded candidates to the formal ledger."""
    if not isinstance(items, list):
        return [], [{"reason": "items不是数组"}]
    text_by_id = {b["block_id"]: str(b.get("text") or "") for b in blocks}
    accepted, rejected = [], []
    for item in items:
        if not isinstance(item, dict):
            rejected.append({"value": item, "reason": "候选不是对象"})
            continue
        requested_ids = [str(x) for x in item.get("evidence_block_ids", [])]
        ids = [block_id for block_id in requested_ids if block_id in valid_ids]
        quote = str(item.get("evidence_quote") or "").strip()
        normalized_quote = evidence_match_text(quote)
        quote_matches = [block_id for block_id, text in text_by_id.items() if normalized_quote and normalized_quote in evidence_match_text(text)]
        location_method = "block_id"
        if not ids and quote_matches:
            ids = quote_matches[:3]
            location_method = "quote_fallback"
        if ids and normalized_quote in evidence_match_text("".join(text_by_id[block_id] for block_id in ids)):
            quote_matches = list(dict.fromkeys([*quote_matches, *ids]))
        statement = str(item.get("statement") or "").strip()
        quality_errors = candidate_quality_errors(statement, quote, ids, quote_matches)
        if quality_errors:
            rejected.append({
                "value": item,
                "reason": ",".join(quality_errors),
                "retryable": True,
            })
            continue
        category = item.get("category") or item.get("primary_category") or "未分类"
        mandatory_signal = item.get("mandatory_signal")
        accepted.append(
            {
                **item,
                "category": category,
                "value": item.get("value", item.get("source_value", "")),
                "mandatory": item.get("mandatory", mandatory_signal == "explicit_mandatory"),
                "evidence_block_ids": ids,
                "evidence_status": "verified",
                "evidence_validation": {
                    "located": bool(ids),
                    "location_method": location_method if ids else "unresolved",
                    "quote_matches_block": True,
                    "recovered_block_ids": [block_id for block_id in ids if block_id not in requested_ids],
                    "invalid_block_ids": [block_id for block_id in requested_ids if block_id not in valid_ids],
                    "reason": None,
                },
            }
        )
    return accepted, rejected


INCOMPLETE_CANDIDATE_END = re.compile(
    r"(?:并在|以及|并且|且|并|或|在|为|符合|标识|包括|如下|下列|[：:、，,])$"
)


def candidate_quality_errors(
    statement: str,
    quote: str,
    block_ids: list[str],
    quote_matches: list[str],
) -> list[str]:
    """Return deterministic admission failures; no model judgement at the trust boundary."""
    errors: list[str] = []
    compact = re.sub(r"\s+", "", statement)
    if len(compact) < 6 or not re.search(r"[\u4e00-\u9fffA-Za-z0-9]", compact):
        errors.append("incomplete_statement")
    elif INCOMPLETE_CANDIDATE_END.search(compact):
        errors.append("incomplete_statement")
    if not quote:
        errors.append("evidence_quote_required")
    if not block_ids:
        errors.append("evidence_block_required")
    elif not any(block_id in quote_matches for block_id in block_ids):
        errors.append("evidence_quote_mismatch")
    return errors


def evidence_match_text(value: Any) -> str:
    """Backward-compatible alias for the shared evidence representation."""
    return canonical_evidence_text(value)


def merge_candidate_items(model_items: Any, hard_facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep model semantics, then fill only missing deterministic fact types."""
    items = list(model_items) if isinstance(model_items, list) else []
    existing = {
        (str(item.get("requirement_type") or ""), tuple(item.get("evidence_block_ids", [])))
        for item in items if isinstance(item, dict)
    }
    return items + [
        item for item in hard_facts
        if (item["requirement_type"], tuple(item["evidence_block_ids"])) not in existing
    ]


def deterministic_hard_facts(role: str, blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract a few exact project facts that should never depend on model recall."""
    if role != "procurement":
        return []
    facts: list[dict[str, Any]] = []
    methods = {
        "公开招标": "open_tender", "邀请招标": "invited_tender", "竞争性磋商": "competitive_consultation",
        "竞争性谈判": "competitive_negotiation", "询价": "inquiry", "单一来源": "single_source",
    }
    for block in blocks:
        block_id = str(block.get("block_id") or "")
        text = plain_evidence_text(block.get("text"))
        if not block_id or not text:
            continue

        project_code = re.search(r"项目编号\s*[:：]?\s*([A-Za-z0-9][A-Za-z0-9._/-]{2,})", text)
        if project_code:
            value = project_code.group(1)
            facts.append(hard_fact(block_id, "project_code", f"项目编号：{value}", "采购项目", "项目编号", value, False))

        method = next(((label, value) for label, value in methods.items() if label in text), None)
        if method:
            label, value = method
            facts.append(hard_fact(block_id, "procurement_method", f"采购方式：{label}", "采购项目", "采购方式", value, False, label))

        deadline = re.search(
            r"((?:响应|投标)截止时间\s*[:：]?\s*20\d{2}年\s*\d{1,2}月\s*\d{1,2}日(?:\s*\d{1,2}:\d{2})?(?:（北京时间）)?)",
            text,
        )
        if deadline:
            quote = re.sub(r"\s+", "", deadline.group(1))
            value = re.sub(r"^(?:响应|投标)截止时间[:：]?", "", quote)
            facts.append(hard_fact(block_id, "submission_deadline", quote, "供应商", "提交响应文件", value, True, value))
    return facts


def hard_fact(
    block_id: str, requirement_type: str, statement: str, subject: str,
    obj: str, value: str, mandatory: bool, source_value: str | None = None,
) -> dict[str, Any]:
    fingerprint = hashlib.sha1(f"{block_id}|{requirement_type}|{value}".encode()).hexdigest()[:10]
    return {
        "candidate_id": f"HARD-{fingerprint}",
        "primary_category": "项目与日程",
        "category_tags": ["项目与日程"],
        "category": "项目与日程",
        "requirement_type": requirement_type,
        "statement": statement,
        "subject": subject,
        "action": "明确" if not mandatory else "提交",
        "object": obj,
        "condition": None,
        "source_value": source_value or value,
        "normalized_value": None,
        "mandatory_signal": "explicit_mandatory" if mandatory else "explicit_fact",
        "mandatory": mandatory,
        "response_materials": [],
        "evidence_block_ids": [block_id],
        "evidence_quote": statement,
        "confidence": 1.0,
    }


def normalize_text(text: str) -> str:
    """生成候选去重键，保留数字和否定词。"""
    return re.sub(r"[\s，。；：、,.;:（）()]+", "", str(text)).lower()


def procurement_assertions(value: Any) -> list[dict[str, Any]]:
    """Read current three-layer ledgers and legacy flat checkpoint artifacts."""
    if isinstance(value, list):
        return value
    return value.get("source_assertions", []) if isinstance(value, dict) else []


def similarity(left: str, right: str) -> float:
    """用中文字符二元组计算可解释相似度，MVP用于候选对齐初筛。"""
    def grams(text: str) -> set[str]:
        value = normalize_text(text)
        return {value[i : i + 2] for i in range(max(len(value) - 1, 0))}

    a, b = grams(left), grams(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def derive_task_legal_facts(
    documents: dict[str, dict[str, Any]], ledger: list[dict[str, Any]], task_context: dict[str, Any]
) -> dict[str, Any]:
    """Derive only explicit procurement facts; absence always remains unknown."""
    sources = []
    for role, document in documents.items():
        for block in document.get("blocks", []):
            text = str(block.get("text") or "")
            if text:
                sources.append({"source": "document", "role": role, "block_id": block.get("block_id"), "quote": text})
    for item in ledger:
        text = str(item.get("statement") or item.get("evidence_quote") or "")
        if text:
            sources.append({"source": "ledger", "item_id": item.get("item_id"), "quote": text})
    project = task_context.get("project", {}) if isinstance(task_context, dict) else {}
    for field in ("name", "project_code", "title"):
        if project.get(field):
            sources.append({"source": "project", "field": field, "quote": str(project[field])})
    if isinstance(task_context, dict) and task_context.get("title"):
        sources.append({"source": "task", "field": "title", "quote": str(task_context["title"])})

    def evidence_for(*terms: str) -> list[dict[str, Any]]:
        return [source for source in sources if any(term in source["quote"] for term in terms)][:5]

    def choice(mapping: list[tuple[str, tuple[str, ...]]]) -> tuple[str, list[dict[str, Any]]]:
        matched = [(value, evidence_for(*terms)) for value, terms in mapping if evidence_for(*terms)]
        values = {value for value, _ in matched}
        return (matched[0] if len(values) == 1 else ("unknown", [])) if matched else ("unknown", [])

    project_type, project_type_evidence = choice([
        ("engineering", ("建设工程", "工程项目", "工程采购")),
        ("goods", ("货物采购", "设备采购", "物资采购")),
        ("services", ("服务采购", "咨询服务", "技术服务")),
    ])
    procurement_method, procurement_method_evidence = choice([
        ("open_tender", ("公开招标",)), ("invited_tender", ("邀请招标",)),
        ("competitive_consultation", ("竞争性磋商",)),
        ("competitive_negotiation", ("竞争性谈判",)), ("inquiry", ("询价",)),
        ("single_source", ("单一来源",)),
    ])

    def boolean_fact(yes: tuple[str, ...], no: tuple[str, ...]) -> tuple[str, list[dict[str, Any]]]:
        no_evidence, yes_evidence = evidence_for(*no), evidence_for(*yes)
        if no_evidence and not yes_evidence:
            return "no", no_evidence
        if yes_evidence and not no_evidence:
            return "yes", yes_evidence
        return "unknown", []

    government, government_evidence = boolean_fact(("政府采购",), ("非政府采购", "不属于政府采购"))
    engineering, engineering_evidence = boolean_fact(("建设工程", "工程项目", "工程采购"), ("非工程", "不属于工程"))
    mandatory, mandatory_evidence = boolean_fact(("依法必须招标", "必须招标项目"), ("非依法必须招标", "不属于依法必须招标"))
    regions = [(match.group(0), source) for source in sources for match in re.finditer(r"[\u4e00-\u9fff]{2,8}(?:省|自治区|市)", source["quote"])]
    region_values = {value for value, _ in regions}
    region, region_evidence = (next(iter(region_values)), [source for value, source in regions if value == next(iter(region_values))][:3]) if len(region_values) == 1 else ("unknown", [])
    facts = {
        "project_type": project_type,
        "procurement_method": procurement_method,
        "is_government_procurement": government,
        "is_engineering_related": engineering,
        "is_mandatory_tender": mandatory,
        "region": region,
        "review_stage": "procurement_document_review",
        "evidence": {
            "project_type": project_type_evidence, "procurement_method": procurement_method_evidence,
            "is_government_procurement": government_evidence, "is_engineering_related": engineering_evidence,
            "is_mandatory_tender": mandatory_evidence, "region": region_evidence, "review_stage": [],
        },
    }
    return facts


def match_legal_documents(facts: dict[str, Any], documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Use only explicit profile predicates; ambiguous profiles stay potential."""
    decisions = []
    for document in documents:
        profile = document.get("applicability", {})
        conditions = profile_conditions(profile)
        reasons, missing, outcomes = [], [], []
        for field, expected, profile_item in conditions:
            actual = facts.get(field, "unknown")
            outcome = "insufficient" if actual == "unknown" else "match" if actual == expected else "mismatch"
            if outcome == "insufficient":
                missing.append(field)
            outcomes.append(outcome)
            reasons.append({"field": field, "expected": expected, "actual": actual, "outcome": outcome, "profile_value": profile_item.get("value")})
        if any(value == "mismatch" for value in outcomes):
            status = "not_applicable"
        elif any(value == "insufficient" for value in outcomes):
            status = "insufficient_facts"
        elif conditions:
            status = "applicable"
        else:
            status = "potential"
            missing.append("applicability_semantics")
            reasons.append({"field": "applicability", "expected": "explicit project predicate", "actual": "not_available", "outcome": "potential"})
        source = document["source"]
        decisions.append({
            "document_key": source["document_key"], "title": source.get("title"), "status": status,
            "reasons": reasons,
            "evidence": {"task_facts": {field: facts.get("evidence", {}).get(field, []) for field, _, _ in conditions}, "profile": [item for _, _, item in conditions]},
            "missing_facts": sorted(set(missing)), "source_freeze": source["source_freeze"], "_units": document.get("units", []),
        })
    return decisions


def profile_conditions(profile: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    conditions = []
    for field in ("project_types", "activities", "trigger_conditions", "business_phases"):
        for item in profile.get(field, []) if isinstance(profile.get(field), list) else []:
            value = str(item.get("value") or "")
            if "政府采购" in value:
                conditions.append(("is_government_procurement", "yes", item))
            elif "依法必须招标" in value or "必须招标项目" in value:
                conditions.append(("is_mandatory_tender", "yes", item))
            elif "工程" in value:
                conditions.append(("is_engineering_related", "yes", item))
            elif "采购文件" in value:
                conditions.append(("review_stage", "procurement_document_review", item))
            elif "公开招标" in value:
                conditions.append(("procurement_method", "open_tender", item))
    return conditions


def _batch_budget(config: dict[str, Any]) -> BatchBudget:
    return BatchBudget(
        model_tokens=int(config.get("model_tokens", 16_000)),
        output_tokens=int(config.get("output_tokens", 3_000)),
        safety_tokens=int(config.get("safety_tokens", 1_000)),
        input_overhead_tokens=int(config.get("input_overhead_tokens", 9_000)),
        primary_block_limit=int(config.get("max_primary_blocks", 25)),
        candidate_limit=int(config.get("max_candidate_estimate", 20)),
        table_row_limit=int(config.get("max_table_rows", 16)),
    )


def _table_retry_ranges(document: dict[str, Any], report: dict[str, Any]) -> list[tuple[int, int]]:
    """Return small, context-padded page ranges around malformed tables."""
    by_id = {block.get("block_id"): block for block in document.get("blocks", [])}
    bad_pages = sorted({
        int(by_id[block_id].get("page_no") or 0)
        for issue in report.get("issues", []) if issue.get("code") == "TABLE_STRUCTURE"
        for block_id in issue.get("block_ids", []) if block_id in by_id and by_id[block_id].get("page_no")
    })
    if not bad_pages:
        raise ValueError("表格重解析缺少可定位页码")
    max_page = max((int(block.get("page_no") or 0) for block in document.get("blocks", [])), default=max(bad_pages))
    groups: list[list[int]] = []
    for page in bad_pages:
        if not groups or page > groups[-1][-1] + 1:
            groups.append([page])
        else:
            groups[-1].append(page)
    padded = [(max(1, group[0] - 1), min(max_page, group[-1] + 1)) for group in groups]
    merged: list[tuple[int, int]] = []
    for start, end in padded:
        if merged and start <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _extraction_failure_finding(role: str, batch_no: int, failure: dict[str, Any]) -> dict[str, Any]:
    """Expose a missing extraction batch without inventing document evidence."""
    return {
        "finding_type": "extraction_quality",
        "risk_level": "unknown",
        "title": f"第{batch_no}批内容未完成自动提取",
        "description": "模型服务或请求预算异常，本批内容未形成完整自动审查结论。",
        "rationale": failure.get("message"),
        "recommendation": "在现有经办和主责复核环节查看该批原文后确认。",
        "document_role": role,
        "source_batch": batch_no,
        "evidence_block_ids": [],
        "evidence_quotes": [],
        "rule_ids": [],
        "legal_unit_ids": [],
        "confidence": 0.0,
        "needs_human_confirmation": True,
    }


def _merge_page_retry(
    original: dict[str, Any], partial: dict[str, Any], start_page: int, end_page: int, role: str
) -> dict[str, Any]:
    """Replace only the requested pages while retaining the initial Pipeline parse elsewhere."""
    replacements = [dict(block) for block in partial.get("blocks", [])]
    if not replacements:
        raise ValueError(f"Hybrid 未返回第 {start_page}-{end_page} 页内容")
    page_numbers = [int(block.get("page_no") or 0) for block in replacements]
    range_length = end_page - start_page + 1
    if not all(start_page <= page <= end_page for page in page_numbers) and all(
        1 <= page <= range_length for page in page_numbers
    ):
        for block in replacements:
            block["page_no"] = int(block.get("page_no") or 0) + start_page - 1
    replacements = [block for block in replacements if start_page <= int(block.get("page_no") or 0) <= end_page]
    if not replacements:
        raise ValueError(f"Hybrid 返回内容与第 {start_page}-{end_page} 页不匹配")

    kept = [
        dict(block) for block in original.get("blocks", [])
        if not start_page <= int(block.get("page_no") or 0) <= end_page
    ]
    for index, block in enumerate(replacements, start=1):
        raw_id = str(block.get("source_block_id") or block.get("block_id") or index).split(":")[-1]
        block["source_block_id"] = raw_id
        block["block_id"] = f"{role}:HYBRID-P{int(block.get('page_no') or 0):04d}-{index:04d}"
    blocks = sorted(
        [*kept, *replacements],
        key=lambda block: (int(block.get("page_no") or 0), int(block.get("reading_order") or 0)),
    )
    for index, block in enumerate(blocks, start=1):
        block["reading_order"] = index
    merged = {**original, "blocks": blocks}
    parser = dict(original.get("parser") or {})
    parser.setdefault("localized_retries", []).append({
        "backend": (partial.get("parser") or {}).get("backend"),
        "effort": (partial.get("parser") or {}).get("effort"),
        "pages": [start_page, end_page],
    })
    merged["parser"] = parser
    merged.pop("quality_actions", None)
    return merged


def rank_legal_units(
    units: list[dict[str, Any]], procurement_items: list[dict[str, Any]], top_k: int
) -> list[dict[str, Any]]:
    """按受控主题、同义词、条号引用和二元组相似度依次召回法规条款。"""
    statements = [str(item.get("statement") or "") for item in procurement_items if item.get("statement")]
    structured_topics = {
        topic
        for item in procurement_items
        for topic in (
            topic_keys(item.get("topics"))
            or ({canonical_topic(item.get("requirement_type"))} if canonical_topic(item.get("requirement_type")) != "other" else set())
        )
    }
    synonym_topics = {
        topic
        for statement in statements
        for topic in topic_keys(dictionary_topics(statement))
    } - structured_topics
    ranked = []
    for unit in units:
        search_text = str(unit.get("search_text") or unit.get("text") or "")
        unit_topics = topic_keys(unit.get("topics")) or topic_keys(dictionary_topics(search_text))
        exact = bool(structured_topics & unit_topics)
        synonym = bool(synonym_topics & unit_topics)
        article = bool(unit.get("article_no") and any(str(unit["article_no"]) in statement for statement in statements))
        fallback = max((similarity(search_text, statement) for statement in statements), default=0.0)
        order = (int(exact), int(synonym), int(article), fallback)
        if any(order):
            ranked.append((order, unit, {
                "topic_exact": sorted(structured_topics & unit_topics),
                "topic_synonym": sorted(synonym_topics & unit_topics),
                "article_reference": article,
                "bigram_similarity": round(fallback, 4),
            }))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [
        {
            **unit,
            "retrieval_score": 4.0 if order[0] else 3.0 if order[1] else 2.0 if order[2] else round(order[3], 4),
            "retrieval_signals": signals,
        }
        for order, unit, signals in ranked[:top_k]
    ]


def build_alignment_matrix(
    baseline: list[dict[str, Any]], candidates: list[dict[str, Any]], target: str
) -> list[dict[str, Any]]:
    """为每个基准事项选择最相关候选，保留证据不足而不直接作最终判定。"""
    matrix = []
    for base in baseline:
        ranked = sorted(
            ((similarity(base.get("statement", ""), item.get("statement", "")), item) for item in candidates),
            key=lambda pair: pair[0],
            reverse=True,
        )
        score, best = ranked[0] if ranked else (0.0, None)
        status = "candidate_found" if score >= 0.12 else "evidence_insufficient"
        matrix.append(
            {
                "baseline_item_id": base.get("item_id"),
                "baseline_statement": base.get("statement"),
                f"{target}_item_id": best.get("item_id") if best and status == "candidate_found" else None,
                f"{target}_statement": best.get("statement") if best and status == "candidate_found" else None,
                "retrieval_score": round(score, 4),
                "status": status,
                "baseline_evidence_block_ids": base.get("evidence_block_ids", []),
                "candidate_evidence_block_ids": best.get("evidence_block_ids", []) if best and status == "candidate_found" else [],
            }
        )
    return matrix


def deduplicate_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按问题类型和标题去重，并合并可追溯ID。"""
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in findings:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        key = (str(item.get("finding_type") or ""), str(item.get("title") or "").strip())
        if key not in unique:
            unique[key] = item
            continue
        current = unique[key]
        for field in ("evidence_block_ids", "legal_unit_ids", "rule_ids", "source_candidate_ids"):
            current[field] = list(dict.fromkeys([*current.get(field, []), *item.get(field, [])]))
    return list(unique.values())


def collect_system_warnings(quality: dict[str, Any], extraction: dict[str, Any]) -> list[dict[str, Any]]:
    """将解析、OCR和提取告警放入独立系统质量分栏。"""
    warnings = [
        {**finding, "review_scope": "system_quality"}
        for report in quality.get("quality", {}).values()
        for finding in report.get("quality_findings", [])
    ] + [
        {**finding, "review_scope": "system_quality"}
        for finding in extraction.get("extraction_findings", [])
    ]
    return deduplicate_findings(warnings)
