"""采购断言和法律单元共用的受控主题。"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


@lru_cache(maxsize=1)
def topic_vocabulary() -> dict[str, dict[str, list[str]]]:
    path = Path(__file__).resolve().parents[1] / "topic_vocabulary.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    topics = payload.get("topics")
    if not isinstance(topics, dict) or "other" not in topics:
        raise ValueError("topic_vocabulary.json must define topics and other")
    seen: dict[str, str] = {}
    for key, definition in topics.items():
        aliases = [key, *definition.get("aliases", [])]
        for alias in aliases:
            if alias in seen and seen[alias] != key:
                raise ValueError(f"duplicate topic alias: {alias}")
            seen[alias] = key
    return topics


def canonical_topic(value: Any) -> str:
    raw = str(value or "").strip()
    for key, definition in topic_vocabulary().items():
        if raw == key or raw in definition.get("aliases", []):
            return key
    return "other"


def dictionary_topics(text: Any) -> list[dict[str, Any]]:
    value = str(text or "")
    matches = []
    for key, definition in topic_vocabulary().items():
        terms = [term for term in definition.get("terms", []) if term and term in value]
        if terms:
            matches.append({"key": key, "source": "dictionary", "matched_terms": terms})
    return matches


def assertion_topics(requirement_type: Any, text: Any) -> tuple[str, list[dict[str, Any]]]:
    canonical = canonical_topic(requirement_type)
    topics = dictionary_topics(text)
    if canonical != "other":
        topics = [
            {"key": canonical, "source": "requirement_type", "matched_terms": [str(requirement_type)]},
            *(item for item in topics if item["key"] != canonical),
        ]
    return canonical, topics


def topic_keys(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    keys = {
        str(item.get("key")) if isinstance(item, dict) else str(item)
        for item in value
        if item and (not isinstance(item, dict) or item.get("key"))
    }
    return {key for key in keys if canonical_topic(key) != "other"}
