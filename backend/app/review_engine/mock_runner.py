"""Deterministic development runner derived from the migrated procurement engine contract."""

from hashlib import sha256
from pathlib import Path


def run_procurement_mock(path: str) -> list[dict]:
    document = Path(path)
    data = document.read_bytes()
    if not data:
        raise ValueError("上传文件为空")
    quote = data[:240].decode("utf-8", errors="replace").strip() or document.name
    return [{
        "title": "采购文件候选审查项",
        "risk_level": "unknown",
        "description": "迁移后的采购审查 mock 已生成候选项，需人工确认。",
        "recommendation": "核对原文条款与适用规则。",
        "source": {"page": None, "section_path": [], "quote": quote, "block_id": f"mock:{sha256(data).hexdigest()[:16]}"},
        "rule_refs": [],
    }]
