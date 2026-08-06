"""确定性检查与证据真实性校验 Tool。"""

from typing import Any

from .schemas import ToolContext


def validate_evidence(
    *, context: ToolContext, finding: dict[str, Any], block_index: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """过滤不存在的证据 ID。输入候选问题及 Block 索引；输出证据状态。"""
    requested = [str(value) for value in finding.get("evidence_block_ids", [])]
    valid = [block_id for block_id in requested if block_id in block_index]
    return {
        "valid": bool(valid),
        "evidence_status": "verified" if valid else "insufficient",
        "valid_block_ids": valid,
        "invalid_block_ids": [block_id for block_id in requested if block_id not in block_index],
        "evidence": [{"block_id": block_id, **block_index[block_id]} for block_id in valid],
    }


def run_deterministic_checks(*, context: ToolContext, issues: list[dict[str, Any]]) -> dict[str, Any]:
    """统一包装后端已完成的编号、日期、金额等确定性检查结果。"""
    return {"issues": issues, "issue_count": len(issues)}
