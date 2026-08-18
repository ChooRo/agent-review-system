"""项目唯一配置入口：默认值、JSON、环境变量与基础校验。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


DEFAULTS: dict[str, Any] = {
    "mineru": {
        "api_url": "http://127.0.0.1:8001", "timeout_seconds": 900,
        "backend": "pipeline", "effort": "medium",
        "table_retry_backend": "hybrid-engine", "table_retry_effort": "high",
    },
    "ocr": {"api_url": "", "api_key": "", "model": "", "timeout_seconds": 120},
    "llm": {"api_url": "", "api_key": "", "model": "", "timeout_seconds": 120, "max_retries": 2},
    "rules": {"path": None, "knowledge_root": "../../../knowledge/rules"},
    "workflow": {
        "extract_workers": 2,
        "model_tokens": 16_000,
        "output_tokens": 3_000,
        "safety_tokens": 1_000,
        "input_overhead_tokens": 9_000,
        "max_request_tokens": 8_000,
        "max_primary_blocks": 25,
        "max_candidate_estimate": 20,
        "max_table_rows": 16,
        "extract_timeout_seconds": 240,
        "extract_idle_timeout_seconds": 90,
        "extract_total_timeout_seconds": 240,
        "extract_max_retries": 1,
        "extract_stream": True,
    },
    "runtime": {"runs_root": "../../../runs", "log_level": "INFO"},
}


def load_settings(path: Path | None = None) -> dict[str, Any]:
    """加载并校验配置；优先级为默认值、JSON、环境变量。"""
    value: dict[str, Any] = {}
    if path is not None and path.is_file():
        value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("配置文件必须是 JSON 对象")

    settings = {name: {**defaults, **dict(value.get(name) or {})} for name, defaults in DEFAULTS.items()}
    _apply_legacy_fields(settings, value)
    _apply_environment(settings)
    _resolve_paths(settings, path.parent if path else Path(__file__).parent)
    _validate(settings)
    return settings


def _apply_legacy_fields(settings: dict[str, Any], value: dict[str, Any]) -> None:
    """兼容迁移前的扁平字段，后续可在所有本机配置升级后删除。"""
    if "mineru_api_url" in value:
        settings["mineru"]["api_url"] = value["mineru_api_url"]
    if "mineru_timeout_seconds" in value:
        settings["mineru"]["timeout_seconds"] = value["mineru_timeout_seconds"]
    if "rules_path" in value:
        settings["rules"]["path"] = value["rules_path"]


def _apply_environment(settings: dict[str, Any]) -> None:
    """用环境变量覆盖本机配置，密钥无需写入 JSON。"""
    mappings = {
        "MINERU_API_URL": ("mineru", "api_url"),
        "MINERU_TIMEOUT_SECONDS": ("mineru", "timeout_seconds"),
        "MINERU_BACKEND": ("mineru", "backend"),
        "MINERU_EFFORT": ("mineru", "effort"),
        "MINERU_TABLE_RETRY_BACKEND": ("mineru", "table_retry_backend"),
        "MINERU_TABLE_RETRY_EFFORT": ("mineru", "table_retry_effort"),
        "DEEPSEEK_OCR_API_URL": ("ocr", "api_url"),
        "DEEPSEEK_OCR_API_KEY": ("ocr", "api_key"),
        "DEEPSEEK_OCR_MODEL": ("ocr", "model"),
        "DEEPSEEK_OCR_TIMEOUT_SECONDS": ("ocr", "timeout_seconds"),
        "REVIEW_LLM_API_URL": ("llm", "api_url"),
        "REVIEW_LLM_API_KEY": ("llm", "api_key"),
        "REVIEW_LLM_MODEL": ("llm", "model"),
        "REVIEW_LLM_TIMEOUT_SECONDS": ("llm", "timeout_seconds"),
        "REVIEW_LLM_MAX_RETRIES": ("llm", "max_retries"),
        "REVIEW_RULES_PATH": ("rules", "path"),
        "REVIEW_RULES_KNOWLEDGE_ROOT": ("rules", "knowledge_root"),
        "REVIEW_EXTRACT_WORKERS": ("workflow", "extract_workers"),
        "REVIEW_MODEL_TOKENS": ("workflow", "model_tokens"),
        "REVIEW_OUTPUT_TOKENS": ("workflow", "output_tokens"),
        "REVIEW_SAFETY_TOKENS": ("workflow", "safety_tokens"),
        "REVIEW_INPUT_OVERHEAD_TOKENS": ("workflow", "input_overhead_tokens"),
        "REVIEW_MAX_REQUEST_TOKENS": ("workflow", "max_request_tokens"),
        "REVIEW_MAX_PRIMARY_BLOCKS": ("workflow", "max_primary_blocks"),
        "REVIEW_MAX_CANDIDATE_ESTIMATE": ("workflow", "max_candidate_estimate"),
        "REVIEW_MAX_TABLE_ROWS": ("workflow", "max_table_rows"),
        "REVIEW_EXTRACT_TIMEOUT_SECONDS": ("workflow", "extract_timeout_seconds"),
        "REVIEW_EXTRACT_IDLE_TIMEOUT_SECONDS": ("workflow", "extract_idle_timeout_seconds"),
        "REVIEW_EXTRACT_TOTAL_TIMEOUT_SECONDS": ("workflow", "extract_total_timeout_seconds"),
        "REVIEW_EXTRACT_MAX_RETRIES": ("workflow", "extract_max_retries"),
        "REVIEW_EXTRACT_STREAM": ("workflow", "extract_stream"),
        "REVIEW_RUNS_ROOT": ("runtime", "runs_root"),
        "REVIEW_LOG_LEVEL": ("runtime", "log_level"),
    }
    for variable, (group, key) in mappings.items():
        if os.getenv(variable):
            settings[group][key] = os.environ[variable]


def _resolve_paths(settings: dict[str, Any], base: Path) -> None:
    """把规则库和运行目录解析为相对配置文件的绝对路径。"""
    for group, key in (("rules", "path"), ("rules", "knowledge_root"), ("runtime", "runs_root")):
        raw = settings[group].get(key)
        if raw:
            path = Path(str(raw)).expanduser()
            settings[group][key] = str((base / path).resolve() if not path.is_absolute() else path.resolve())


def _validate(settings: dict[str, Any]) -> None:
    """在程序启动时尽早拒绝明显错误的地址、超时和日志级别。"""
    api_url = str(settings["mineru"].get("api_url") or "")
    if not api_url.startswith(("http://", "https://")):
        raise ValueError("mineru.api_url 必须是 http:// 或 https:// 地址")
    for group in ("mineru", "llm", "ocr"):
        try:
            timeout = int(settings[group]["timeout_seconds"])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{group}.timeout_seconds 必须是正整数") from exc
        if timeout <= 0:
            raise ValueError(f"{group}.timeout_seconds 必须是正整数")
        settings[group]["timeout_seconds"] = timeout
    ocr_url = str(settings["ocr"].get("api_url") or "")
    if ocr_url and not ocr_url.startswith(("http://", "https://")):
        raise ValueError("ocr.api_url 必须是 http:// 或 https:// 地址")
    backends = {"pipeline", "vlm-engine", "hybrid-engine", "vlm-http-client", "hybrid-http-client"}
    for key in ("backend", "table_retry_backend"):
        if settings["mineru"].get(key) not in backends:
            raise ValueError(f"mineru.{key} 不是受支持的解析后端")
    for key in ("effort", "table_retry_effort"):
        if settings["mineru"].get(key) not in {"medium", "high"}:
            raise ValueError(f"mineru.{key} 必须是 medium 或 high")
    try:
        retries = int(settings["llm"].get("max_retries", 0))
    except (TypeError, ValueError) as exc:
        raise ValueError("llm.max_retries 必须是非负整数") from exc
    if retries < 0:
        raise ValueError("llm.max_retries 必须是非负整数")
    settings["llm"]["max_retries"] = retries
    try:
        workers = int(settings["workflow"].get("extract_workers", 3))
    except (TypeError, ValueError) as exc:
        raise ValueError("workflow.extract_workers 必须是1到8之间的整数") from exc
    if not 1 <= workers <= 8:
        raise ValueError("workflow.extract_workers 必须是1到8之间的整数")
    settings["workflow"]["extract_workers"] = workers
    token_fields = ("model_tokens", "output_tokens", "safety_tokens", "input_overhead_tokens", "max_request_tokens")
    try:
        token_budget = {key: int(settings["workflow"][key]) for key in token_fields}
    except (TypeError, ValueError) as exc:
        raise ValueError("workflow Token预算必须是整数") from exc
    if token_budget["model_tokens"] <= sum(token_budget[key] for key in ("output_tokens", "safety_tokens", "input_overhead_tokens")):
        raise ValueError("workflow.model_tokens 必须大于输出、安全和输入固定开销之和")
    if any(value < 0 for key, value in token_budget.items() if key != "model_tokens"):
        raise ValueError("workflow Token预留不能为负数")
    if token_budget["max_request_tokens"] > token_budget["model_tokens"] - token_budget["output_tokens"] - token_budget["safety_tokens"]:
        raise ValueError("workflow.max_request_tokens 超过模型输入安全上限")
    settings["workflow"].update(token_budget)
    positive_fields = (
        "max_primary_blocks", "max_candidate_estimate", "max_table_rows",
        "extract_timeout_seconds", "extract_idle_timeout_seconds", "extract_total_timeout_seconds",
    )
    try:
        limits = {key: int(settings["workflow"][key]) for key in positive_fields}
        extract_retries = int(settings["workflow"]["extract_max_retries"])
    except (TypeError, ValueError) as exc:
        raise ValueError("workflow 提取限制必须是整数") from exc
    if any(value <= 0 for value in limits.values()) or extract_retries < 0:
        raise ValueError("workflow 提取限制必须为正数，重试次数不能为负数")
    if limits["extract_idle_timeout_seconds"] > limits["extract_total_timeout_seconds"]:
        raise ValueError("workflow.extract_idle_timeout_seconds 不能超过总时限")
    settings["workflow"].update(limits)
    settings["workflow"]["extract_max_retries"] = extract_retries
    stream_value = settings["workflow"].get("extract_stream", True)
    settings["workflow"]["extract_stream"] = (
        stream_value if isinstance(stream_value, bool)
        else str(stream_value).strip().lower() in {"1", "true", "yes", "on"}
    )
    level = str(settings["runtime"].get("log_level") or "INFO").upper()
    if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ValueError("runtime.log_level 不是有效日志级别")
    settings["runtime"]["log_level"] = level
