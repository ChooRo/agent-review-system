"""合同三方一致性审查专用 Tool。"""

from typing import Any

from ..schemas import ToolContext


def get_three_party_item(
    *, context: ToolContext, procurement_item: dict[str, Any], response_item: dict[str, Any] | None,
    contract_item: dict[str, Any] | None
) -> dict[str, Any]:
    """作用：组合采购要求、供应商承诺和合同约定供同屏核验。
    输入：context，以及采购、响应、合同三个阶段的对应事项。
    输出：按 procurement/response/contract 分组的三方事项。
    逻辑：保持原数据不变，仅组装统一的三方对照结构。
    """
    return {"procurement": procurement_item, "response": response_item, "contract": contract_item}


def check_commitment_transfer(
    *, context: ToolContext, alignments: list[dict[str, Any]]
) -> dict[str, Any]:
    """作用：检查供应商承诺是否在合同中得到承接。
    输入：context 和三方对照结果 alignments。
    输出：证据不足的事项列表及数量。
    逻辑：筛选 status 为 evidence_insufficient 的对照事项。
    """
    missing = [item for item in alignments if item.get("status") == "evidence_insufficient"]
    return {"missing_or_uncertain": missing, "count": len(missing)}
