"""确定性检查与证据真实性校验 Tool。"""

from typing import Any

from ..schemas import ToolContext


def validate_evidence(
    *, context: ToolContext, finding: dict[str, Any], block_index: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """作用：验证候选问题引用的证据 Block 是否真实存在。
    输入：context、finding 候选问题和 block_index 原文索引。
    输出：验证状态、有效/无效 ID 列表及有效证据内容。
    逻辑：将问题中的证据 ID 与索引求交集，并据此生成验证结果。
    """
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
    """作用：统一包装编号、日期、金额等确定性检查结果。
    输入：context 和后端已生成的 issues。
    输出：原问题列表及问题数量。
    逻辑：不改变检查内容，只补充统一计数字段供 Agent 使用。
    """
    return {"issues": issues, "issue_count": len(issues)}
