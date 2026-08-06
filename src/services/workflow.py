"""全文理解、业务台账和三个审查Agent的可断点MVP流水线。"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from .llm import LLMService
from .mineru import MinerUService
from .runtime import RunStore, read_json, write_json
from tools import ToolContext, build_registry


STEPS = [
    "parse_documents",
    "quality_check",
    "structure_profile",
    "extract_candidates",
    "build_ledger",
    "build_scene_view",
    "global_validation",
    "match_rules",
    "agent_review",
    "validate_evidence",
    "final_report",
]
REQUIRED_ROLES = {
    "procurement": {"procurement"},
    "response": {"procurement", "response"},
    "contract": {"procurement", "response", "contract"},
}
ID_PREFIX = {"procurement": "REQ", "response": "RESP", "contract": "CTR"}


class WorkflowEngine:
    """执行九步审查流水线，并在每一步完成后保存可恢复检查点。"""

    def __init__(
        self,
        runs_root: Path,
        skills_path: Path,
        config: dict[str, Any] | None = None,
    ):
        self.runs_root = runs_root.resolve()
        self.skills = read_json(skills_path)
        self.config = config or {}
        formal_root = skills_path.parent / "skills"
        self.formal_skills = {
            "structure": load_formal_skill(formal_root / "understand-document-structure"),
            "procurement": load_formal_skill(formal_root / "understand-procurement-document"),
            "procurement_review": load_formal_skill(formal_root / "review-procurement-document"),
        }

    def start(
        self,
        scenario: str,
        documents: dict[str, str],
        mode: str = "mock",
        pause_after: str | None = None,
    ) -> RunStore:
        """校验输入、创建运行并执行到完成或指定断点。"""
        validate_request(scenario, documents, mode, pause_after)
        store = RunStore.create(self.runs_root, scenario, documents, mode, pause_after)
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
        llm = LLMService(self.config.get("llm", {}), state["mode"], store)
        mineru_config = self.config.get("mineru", {})
        mineru = MinerUService(
            mineru_config.get("api_url", "http://127.0.0.1:8000"),
            int(mineru_config.get("timeout_seconds", 900)),
        )
        handlers: dict[str, Callable[[RunStore, dict[str, Any], LLMService, MinerUService], Any]] = {
            name: getattr(self, f"_{name}") for name in STEPS
        }
        state["status"] = "running"
        state["error"] = None
        store.save_state(state)
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
        """检查空文档、乱码、空文本和页码覆盖，不把空表格视为异常。"""
        parsed = self._previous(store, "parse_documents")
        reports: dict[str, Any] = {}
        for role, document in parsed["documents"].items():
            blocks = document.get("blocks", [])
            text_blocks = [b for b in blocks if b.get("block_type") in {"heading", "paragraph", "table"}]
            nonempty = [b for b in text_blocks if str(b.get("text") or "").strip()]
            text = "".join(str(b.get("text") or "") for b in nonempty)
            replacement_ratio = text.count("�") / max(len(text), 1)
            empty_ratio = (len(text_blocks) - len(nonempty)) / max(len(text_blocks), 1)
            issues = []
            if not nonempty:
                issues.append({"code": "NO_TEXT", "severity": "error", "message": "没有可审查文本"})
            if replacement_ratio > 0.01:
                issues.append({"code": "GARBLED_TEXT", "severity": "error", "message": "乱码比例过高"})
            if empty_ratio > 0.4:
                issues.append({"code": "MANY_EMPTY_BLOCKS", "severity": "warning", "message": "空文本块比例偏高"})
            status = "reparse_recommended" if any(i["severity"] == "error" for i in issues) else (
                "manual_review" if issues else "reviewable"
            )
            reports[role] = {
                "status": status,
                "block_count": len(blocks),
                "nonempty_block_count": len(nonempty),
                "char_count": len(text),
                "page_count": max([int(b.get("page_no") or 0) for b in blocks] or [0]),
                "replacement_ratio": round(replacement_ratio, 6),
                "empty_ratio": round(empty_ratio, 6),
                "issues": issues,
            }
        return {"quality": reports}

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
            }
            review_batches = structure_review_batches(document.get("blocks", []), role, 12_000)
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
                mock_profile = {
                    "skill": "understand-document-structure",
                    "skill_version": "1.0.0",
                    "document_id": document.get("document_id"),
                    "document_version_id": document.get("document_version_id") or document.get("version_id"),
                    "document_role": role,
                    "quality_status": quality.get(role, {}).get("status"),
                    "outline": [],
                    "section_responsibilities": [],
                    "parties": [],
                    "terms": [],
                    "references": [],
                    "global_constraints": [],
                    "inventories": {"tables": [], "images": [], "attachments": []},
                    "warnings": [],
                    "unresolved": [],
                }
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
                        mock_profile,
                    )
                )
            profiles[role] = merge_structure_profiles(base, partials, quality.get(role, {}))
            profiles[role]["llm_review_batch_count"] = len(review_batches)
        return {"profiles": profiles}

    def _extract_candidates(
        self, store: RunStore, state: dict[str, Any], llm: LLMService, mineru: MinerUService
    ) -> dict[str, Any]:
        """按完整章节批次提取候选原子事项，并要求每项绑定Block。"""
        parsed = self._previous(store, "parse_documents")
        candidates: dict[str, list[dict[str, Any]]] = {}
        workers = int(self.config.get("workflow", {}).get("extract_workers", 3))
        for role, document in parsed["documents"].items():
            skill = self.skills["document_understanding"][role]
            role_items: list[dict[str, Any]] = []
            sections = section_batches(document.get("blocks", []), max_chars=12_000)
            store.event(
                "INFO",
                "extract_candidates",
                "parallel_batches_started",
                f"{role}开始并行提取原子事项",
                batch_count=len(sections),
                workers=min(workers, len(sections)),
            )

            def extract_batch(
                entry: tuple[int, list[dict[str, Any]]]
            ) -> tuple[int, list[dict[str, Any]], list[dict[str, Any]], bool]:
                batch_no, blocks = entry
                valid_ids = {b["block_id"] for b in blocks}
                mock_items = heuristic_candidates(role, blocks, skill["categories"])
                prompt = skill["instruction"]
                result_key = "items"
                mock_result: dict[str, Any] = {"items": mock_items}
                if role == "procurement":
                    prompt = self.formal_skills["procurement"]
                    result_key = "candidate_items"
                    mock_result = {
                        "skill": "understand-procurement-document",
                        "skill_version": "1.0.0",
                        "candidate_items": mock_items,
                        "coverage": [],
                        "rejected_items": [],
                        "unresolved_references": [],
                        "warnings": [],
                    }
                prompt += ("" if role == "procurement" else " 返回严格JSON：{\"items\":[{\"category\":\"\",\"statement\":\"\",\"subject\":\"\",\"action\":\"\",\"condition\":\"\",\"value\":\"\",\"mandatory\":false,\"evidence_block_ids\":[\"B-...\"],\"evidence_quote\":\"原文摘录\"}]}。")
                payload = {
                    "document_role": role,
                    "allowed_categories": skill["categories"],
                    "batch_no": batch_no,
                    **extraction_batch_payload(role, blocks),
                }
                fingerprint = hashlib.sha1(
                    (prompt + json.dumps(payload, ensure_ascii=False, sort_keys=True)).encode("utf-8")
                ).hexdigest()
                checkpoint = (
                    store.run_dir
                    / "batch_artifacts"
                    / "extract_candidates"
                    / f"{role}_{batch_no:03d}.json"
                )
                if checkpoint.is_file():
                    cached = read_json(checkpoint)
                    if cached.get("input_fingerprint") == fingerprint:
                        return batch_no, cached.get("accepted", []), cached.get("rejected", []), True

                result = llm.json_call("extract_candidates", prompt, payload, mock_result)
                accepted, rejected = validate_candidate_items(result.get(result_key, []), valid_ids, blocks)
                for item in accepted:
                    item["source_batch"] = batch_no
                write_json(
                    checkpoint,
                    {
                        "schema_version": 1,
                        "document_role": role,
                        "batch_no": batch_no,
                        "input_fingerprint": fingerprint,
                        "accepted": accepted,
                        "rejected": rejected,
                    },
                )
                return batch_no, accepted, rejected, False

            entries = list(enumerate(sections, start=1))
            with ThreadPoolExecutor(max_workers=min(workers, len(entries))) as executor:
                results = list(executor.map(extract_batch, entries))
            for batch_no, accepted, rejected, reused in results:
                role_items.extend(accepted)
                store.event(
                    "INFO",
                    "extract_candidates",
                    "batch_checkpoint_reused" if reused else "batch_checkpoint_saved",
                    f"{role}第{batch_no}批{'复用' if reused else '保存'}检查点",
                    batch_no=batch_no,
                    accepted_count=len(accepted),
                    rejected_count=len(rejected),
                )
                if rejected:
                    store.event(
                        "WARNING",
                        "extract_candidates",
                        "candidates_rejected",
                        f"{role}第{batch_no}批过滤无法定位候选",
                        count=len(rejected),
                    )
            candidates[role] = role_items
        return {"candidates": candidates}

    def _build_ledger(
        self, store: RunStore, state: dict[str, Any], llm: LLMService, mineru: MinerUService
    ) -> dict[str, Any]:
        """对候选事项去重、标准化并生成稳定台账ID。"""
        raw = self._previous(store, "extract_candidates")["candidates"]
        ledgers: dict[str, list[dict[str, Any]]] = {}
        for role, items in raw.items():
            merged: dict[str, dict[str, Any]] = {}
            for item in items:
                key = normalize_text(item.get("statement") or item.get("evidence_quote") or "")
                if not key:
                    continue
                if key not in merged:
                    merged[key] = {**item, "evidence_block_ids": list(item.get("evidence_block_ids", []))}
                else:
                    existing = merged[key]
                    existing["evidence_block_ids"] = sorted(
                        set(existing["evidence_block_ids"]) | set(item.get("evidence_block_ids", []))
                    )
            ledger = []
            for item in merged.values():
                digest = hashlib.sha1(
                    f"{role}|{normalize_text(item['statement'])}".encode("utf-8")
                ).hexdigest()[:10]
                ledger.append({"item_id": f"{ID_PREFIX[role]}-{digest}", **item})
            ledgers[role] = ledger
        return {"ledgers": ledgers, "stats": {role: len(items) for role, items in ledgers.items()}}

    def _build_scene_view(
        self, store: RunStore, state: dict[str, Any], llm: LLMService, mineru: MinerUService
    ) -> dict[str, Any]:
        """将统一台账投影为七类视图、要求响应矩阵或三方对照。"""
        ledgers = self._previous(store, "build_ledger")["ledgers"]
        scenario = state["scenario"]
        if scenario == "procurement":
            groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for item in ledgers.get("procurement", []):
                groups[item.get("category") or "未分类"].append(item)
            return {"scenario": scenario, "topic_views": dict(groups)}
        if scenario == "response":
            matrix = build_alignment_matrix(
                ledgers.get("procurement", []), ledgers.get("response", []), "response"
            )
            return {"scenario": scenario, "response_matrix": matrix}
        procurement = ledgers.get("procurement", [])
        response = ledgers.get("response", [])
        contract = ledgers.get("contract", [])
        return {
            "scenario": scenario,
            "procurement_contract_matrix": build_alignment_matrix(procurement, contract, "contract"),
            "response_contract_matrix": build_alignment_matrix(response, contract, "contract"),
        }

    def _agent_review(
        self, store: RunStore, state: dict[str, Any], llm: LLMService, mineru: MinerUService
    ) -> dict[str, Any]:
        """调用对应专业Agent配置，生成带Block证据的问题和总体结论。"""
        scenario = state["scenario"]
        view = self._previous(store, "build_scene_view")
        quality = self._previous(store, "quality_check")["quality"]
        global_validation = self._previous(store, "global_validation")
        rules = self._previous(store, "match_rules")
        skill = self.skills["review_agents"][scenario]
        instruction = skill["instruction"]
        if scenario == "procurement":
            instruction = self.formal_skills["procurement_review"]
        mock = mock_review(scenario, view, quality, global_validation)
        result = llm.json_call(
            "agent_review",
            instruction
            + " 返回严格JSON：{\"skill\":\"review-procurement-document\",\"skill_version\":\"1.0.0\",\"overall_conclusion\":\"\",\"coverage_summary\":[],\"findings\":[{\"finding_type\":\"\",\"risk_level\":\"high|medium|low|pending\",\"title\":\"\",\"description\":\"\",\"ledger_item_ids\":[],\"evidence_block_ids\":[],\"evidence_quotes\":[],\"rule_ids\":[],\"legal_unit_ids\":[],\"legal_applicability\":\"not_assessed|applicable|potential|not_applicable|insufficient_metadata\",\"rationale\":\"\",\"recommendation\":\"\",\"confidence\":0.0,\"needs_human_confirmation\":true}],\"unresolved\":[],\"warnings\":[]}。",
            {
                "scenario": scenario,
                "quality": quality,
                "global_validation": global_validation,
                "matched_rules": rules,
                "scene_view": limit_view(view),
            },
            mock,
        )
        return {
            "agent": skill["name"],
            "overall_conclusion": result.get("overall_conclusion", "待人工确认"),
            "findings": result.get("findings", []),
        }

    def _global_validation(
        self, store: RunStore, state: dict[str, Any], llm: LLMService, mineru: MinerUService
    ) -> dict[str, Any]:
        """执行不依赖LLM的缺项、引用和关键时间冲突检查。"""
        parsed = self._previous(store, "parse_documents")
        view = self._previous(store, "build_scene_view")
        issues: list[dict[str, Any]] = []
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
            full_text = "\n".join(str(block.get("text") or "") for block in blocks)
            references: dict[str, list[str]] = defaultdict(list)
            events: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
            for block in blocks:
                text = str(block.get("text") or "")
                for reference in re.findall(r"附件\s*[一二三四五六七八九十\d]+", text):
                    references[reference.replace(" ", "")].append(block["block_id"])
                for event_name in ("投标截止时间", "响应截止时间", "开标时间"):
                    if event_name in text:
                        for date in re.findall(r"20\d{2}[年./-]\d{1,2}[月./-]\d{1,2}日?", text):
                            events[event_name][date].append(block["block_id"])
            for reference, block_ids in references.items():
                normalized = reference.replace(" ", "")
                if full_text.replace(" ", "").count(normalized) == 1:
                    issues.append(
                        {
                            "code": "POSSIBLE_UNRESOLVED_REFERENCE",
                            "message": f"{role}中的{reference}仅出现一次，可能缺少被引用附件",
                            "evidence_block_ids": block_ids,
                            "needs_human_confirmation": True,
                        }
                    )
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
        return {"issues": issues, "issue_count": len(issues)}

    def _match_rules(
        self, store: RunStore, state: dict[str, Any], llm: LLMService, mineru: MinerUService
    ) -> dict[str, Any]:
        """筛选可执行规则，并从法规知识目录召回相关条款单元。"""
        config = self.config.get("rules", {})
        rules_path = config.get("path")
        rules: list[dict[str, Any]] = []
        rule_source = None
        if rules_path:
            path = Path(str(rules_path)).expanduser().resolve()
            if not path.is_file():
                raise FileNotFoundError(f"规则文件不存在：{path}")
            value = read_json(path)
            rules = value.get("rules", value) if isinstance(value, dict) else value
            if not isinstance(rules, list):
                raise ValueError("规则文件必须是数组或含rules数组的对象")
            rule_source = str(path)
        view_text = json.dumps(self._previous(store, "build_scene_view"), ensure_ascii=False)
        matched = []
        for rule in rules:
            if not isinstance(rule, dict) or rule.get("status", "effective") != "effective":
                continue
            applies = rule.get("applies_to", [])
            if applies and state["scenario"] not in applies:
                continue
            keywords = [str(word) for word in rule.get("keywords", [])]
            if not keywords or any(word in view_text for word in keywords):
                matched.append(rule)

        legal_units: list[dict[str, Any]] = []
        legal_sources: list[dict[str, Any]] = []
        knowledge_root = config.get("knowledge_root")
        if knowledge_root and state["scenario"] == "procurement":
            root = Path(str(knowledge_root)).expanduser().resolve()
            for knowledge_path in sorted(root.glob("*/legal_knowledge.json")) if root.is_dir() else []:
                knowledge = read_json(knowledge_path)
                document = knowledge.get("legal_document", {})
                quality = knowledge.get("quality", {})
                legal_sources.append({
                    "path": str(knowledge_path),
                    "title": document.get("title"),
                    "status": document.get("status"),
                    "effective_date": document.get("effective_date"),
                    "quality_status": quality.get("status"),
                })
                legal_units.extend(knowledge.get("units", []))
        procurement_items = self._previous(store, "build_ledger")["ledgers"].get("procurement", [])
        ranked_units = rank_legal_units(legal_units, procurement_items, top_k=30)
        warnings = []
        if not rules_path:
            warnings.append("未配置可执行规则库")
        if not ranked_units:
            warnings.append("未召回法规条款")
        if any(source.get("quality_status") != "reviewable" for source in legal_sources):
            warnings.append("部分法规效力元数据未确认，只能作为候选依据")
        for warning in warnings:
            store.event("WARNING", "match_rules", "rule_warning", warning)
        return {
            "rules": matched,
            "matched_count": len(matched),
            "rule_source": rule_source,
            "legal_units": ranked_units,
            "legal_unit_count": len(ranked_units),
            "legal_sources": legal_sources,
            "warnings": warnings,
        }

    def _validate_evidence(
        self, store: RunStore, state: dict[str, Any], llm: LLMService, mineru: MinerUService
    ) -> dict[str, Any]:
        """过滤不存在的Block证据，无证据问题降级为待人工确认。"""
        parsed = self._previous(store, "parse_documents")
        review = self._previous(store, "agent_review")
        matched_rules = self._previous(store, "match_rules")
        block_index = {
            b["block_id"]: {
                "document_role": role,
                "page_no": b.get("page_no"),
                "bbox": b.get("bbox"),
                "quote": str(b.get("text") or "")[:300],
            }
            for role, document in parsed["documents"].items()
            for b in document.get("blocks", [])
        }
        registry = build_registry(store.event)
        context = ToolContext(run_id=state["run_id"], agent="workflow")
        legal_index = {
            unit["legal_unit_id"]: unit
            for unit in matched_rules.get("legal_units", [])
            if unit.get("legal_unit_id")
        }
        findings = []
        for index, finding in enumerate(review.get("findings", []), start=1):
            result = registry.call("validate_evidence", context, finding=finding, block_index=block_index)
            if result.status != "success":
                raise RuntimeError(result.data.get("message", "证据校验 Tool 调用失败"))
            ids = result.data["valid_block_ids"]
            evidence = result.data["evidence"]
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
                "evidence_status": result.data["evidence_status"],
                "legal_unit_ids": legal_ids,
                "legal_evidence": legal_evidence,
                "legal_evidence_status": "verified" if legal_evidence else "not_cited",
            }
            if not evidence:
                validated["needs_human_confirmation"] = True
                validated["risk_level"] = "pending"
            if finding.get("finding_type") == "legal_risk" and not legal_evidence:
                validated["needs_human_confirmation"] = True
                validated["legal_evidence_status"] = "insufficient"
            findings.append(validated)
        return {
            "agent": review["agent"],
            "overall_conclusion": review["overall_conclusion"],
            "findings": findings,
            "verified_count": sum(f["evidence_status"] == "verified" for f in findings),
            "insufficient_count": sum(f["evidence_status"] == "insufficient" for f in findings),
        }

    def _final_report(
        self, store: RunStore, state: dict[str, Any], llm: LLMService, mineru: MinerUService
    ) -> dict[str, Any]:
        """汇总本次MVP结果，不替代经办和监督的最终确认。"""
        evidence = self._previous(store, "validate_evidence")
        ledger = self._previous(store, "build_ledger")
        view = self._previous(store, "build_scene_view")
        rules = self._previous(store, "match_rules")
        return {
            "run_id": state["run_id"],
            "scenario": state["scenario"],
            "mode": state["mode"],
            "overall_conclusion": evidence["overall_conclusion"],
            "finding_count": len(evidence["findings"]),
            "verified_finding_count": evidence["verified_count"],
            "evidence_insufficient_count": evidence["insufficient_count"],
            "ledger_stats": ledger["stats"],
            "scene_view": view,
            "legal_basis_summary": {
                "candidate_legal_unit_count": rules.get("legal_unit_count", 0),
                "sources": rules.get("legal_sources", []),
                "warnings": rules.get("warnings", []),
            },
            "findings": evidence["findings"],
            "human_review_required": True,
        }


def validate_request(scenario: str, documents: dict[str, str], mode: str, pause_after: str | None) -> None:
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
    if mode not in {"mock", "live"}:
        raise ValueError("mode必须是mock或live")
    if pause_after is not None and pause_after not in STEPS:
        raise ValueError(f"未知断点：{pause_after}")


def load_formal_skill(skill_dir: Path) -> str:
    """加载正式SKILL.md及其直接references，供项目运行时调用。"""
    skill_path = skill_dir / "SKILL.md"
    if not skill_path.is_file():
        raise FileNotFoundError(f"正式Skill不存在：{skill_path}")
    parts = [skill_path.read_text(encoding="utf-8")]
    references = skill_dir / "references"
    if references.is_dir():
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
        "references": [],
        "global_constraints": [],
        "inventories": {
            name: list(base.get("inventories", {}).get(name, []))
            for name in ("tables", "images", "attachments")
        },
        "warnings": list(quality.get("issues", [])),
        "unresolved": [],
    }
    for key in ("section_responsibilities", "parties", "terms", "references", "global_constraints", "warnings", "unresolved"):
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
                "block_id": block.get("block_id"),
                "type": block.get("block_type"),
                "page": block.get("page_no"),
                "text": block.get("text", ""),
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


def heuristic_candidates(role: str, blocks: list[dict[str, Any]], categories: list[str]) -> list[dict[str, Any]]:
    """mock模式使用的可解释候选提取，验证流程而不冒充正式AI效果。"""
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
        category = classify_category(role, text, categories)
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


def classify_category(role: str, text: str, categories: list[str]) -> str:
    """mock模式按关键词给候选打标签；live模式由文档理解Skill判断。"""
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
    """仅接受能映射到当前批次真实Block的候选。"""
    if not isinstance(items, list):
        return [], [{"reason": "items不是数组"}]
    text_by_id = {b["block_id"]: str(b.get("text") or "") for b in blocks}
    accepted, rejected = [], []
    for item in items:
        if not isinstance(item, dict):
            rejected.append({"value": item, "reason": "候选不是对象"})
            continue
        ids = [str(x) for x in item.get("evidence_block_ids", []) if str(x) in valid_ids]
        quote = str(item.get("evidence_quote") or "").strip()
        if not ids or not quote or not any(quote in text_by_id[bid] for bid in ids):
            rejected.append({**item, "reason": "无法定位原文"})
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
            }
        )
    return accepted, rejected


def normalize_text(text: str) -> str:
    """生成候选去重键，保留数字和否定词。"""
    return re.sub(r"[\s，。；：、,.;:（）()]+", "", str(text)).lower()


def similarity(left: str, right: str) -> float:
    """用中文字符二元组计算可解释相似度，MVP用于候选对齐初筛。"""
    def grams(text: str) -> set[str]:
        value = normalize_text(text)
        return {value[i : i + 2] for i in range(max(len(value) - 1, 0))}

    a, b = grams(left), grams(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def rank_legal_units(
    units: list[dict[str, Any]], procurement_items: list[dict[str, Any]], top_k: int
) -> list[dict[str, Any]]:
    """按法规检索文本与采购台账事项的最高二元组相似度召回候选条款。"""
    statements = [str(item.get("statement") or "") for item in procurement_items if item.get("statement")]
    ranked = []
    for unit in units:
        search_text = str(unit.get("search_text") or unit.get("text") or "")
        score = max((similarity(search_text, statement) for statement in statements), default=0.0)
        if score > 0:
            ranked.append((score, unit))
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    return [{**unit, "retrieval_score": round(score, 4)} for score, unit in ranked[:top_k]]


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


def mock_review(
    scenario: str,
    view: dict[str, Any],
    quality: dict[str, Any],
    global_validation: dict[str, Any],
) -> dict[str, Any]:
    """生成流程验收用结果，并明确标记mock不能替代正式审查。"""
    findings: list[dict[str, Any]] = []
    for role, report in quality.items():
        for issue in report.get("issues", []):
            findings.append(
                {
                    "finding_type": "parse_quality",
                    "risk_level": "medium",
                    "description": f"{role}：{issue['message']}",
                    "ledger_item_ids": [],
                    "evidence_block_ids": [],
                    "recommendation": "人工确认解析质量或重新解析",
                    "needs_human_confirmation": True,
                }
            )
    for issue in global_validation.get("issues", []):
        findings.append(
            {
                "finding_type": issue.get("code", "global_validation"),
                "risk_level": "pending",
                "description": issue.get("message", "全文检查异常"),
                "ledger_item_ids": [],
                "evidence_block_ids": issue.get("evidence_block_ids", []),
                "recommendation": "回看原文并人工确认",
                "needs_human_confirmation": True,
            }
        )
    if scenario != "procurement":
        matrices = [value for key, value in view.items() if key.endswith("matrix")]
        for row in [row for matrix in matrices for row in matrix if row.get("status") == "evidence_insufficient"][:50]:
            findings.append(
                {
                    "finding_type": "evidence_insufficient",
                    "risk_level": "pending",
                    "description": "未找到足够的对应证据，需扩大检索或人工确认",
                    "ledger_item_ids": [row.get("baseline_item_id")],
                    "evidence_block_ids": row.get("baseline_evidence_block_ids", []),
                    "recommendation": "检查条款号、关键词、表格和附件",
                    "needs_human_confirmation": True,
                }
            )
    return {
        "overall_conclusion": "mock模式已跑通流程，所有业务结论均需live模型和人工复核",
        "findings": findings,
    }


def limit_view(view: dict[str, Any], max_items: int = 120) -> dict[str, Any]:
    """限制单次Agent调用规模，完整场景视图仍保存在步骤产物中。"""
    limited: dict[str, Any] = {}
    for key, value in view.items():
        if isinstance(value, list):
            limited[key] = value[:max_items]
        elif isinstance(value, dict):
            limited[key] = {name: items[:30] if isinstance(items, list) else items for name, items in value.items()}
        else:
            limited[key] = value
    return limited
