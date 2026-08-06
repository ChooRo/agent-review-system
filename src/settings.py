"""项目唯一配置入口：默认值、JSON、环境变量与基础校验。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


DEFAULTS: dict[str, Any] = {
    "mineru": {"api_url": "http://127.0.0.1:8000", "timeout_seconds": 900},
    "llm": {"api_url": "", "api_key": "", "model": "", "timeout_seconds": 120, "max_retries": 0},
    "rules": {"path": None, "knowledge_root": "../knowledge/rules"},
    "workflow": {"extract_workers": 3},
    "runtime": {"runs_root": "../runs", "log_level": "INFO"},
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
        "REVIEW_LLM_API_URL": ("llm", "api_url"),
        "REVIEW_LLM_API_KEY": ("llm", "api_key"),
        "REVIEW_LLM_MODEL": ("llm", "model"),
        "REVIEW_LLM_TIMEOUT_SECONDS": ("llm", "timeout_seconds"),
        "REVIEW_LLM_MAX_RETRIES": ("llm", "max_retries"),
        "REVIEW_RULES_PATH": ("rules", "path"),
        "REVIEW_RULES_KNOWLEDGE_ROOT": ("rules", "knowledge_root"),
        "REVIEW_EXTRACT_WORKERS": ("workflow", "extract_workers"),
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
    for group in ("mineru", "llm"):
        try:
            timeout = int(settings[group]["timeout_seconds"])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{group}.timeout_seconds 必须是正整数") from exc
        if timeout <= 0:
            raise ValueError(f"{group}.timeout_seconds 必须是正整数")
        settings[group]["timeout_seconds"] = timeout
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
    level = str(settings["runtime"].get("log_level") or "INFO").upper()
    if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ValueError("runtime.log_level 不是有效日志级别")
    settings["runtime"]["log_level"] = level
