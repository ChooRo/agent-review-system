"""合同三方一致性审查专用 Tool。"""

from typing import Any

from .schemas import ToolContext


def get_three_party_item(
    *, context: ToolContext, procurement_item: dict[str, Any], response_item: dict[str, Any] | None,
    contract_item: dict[str, Any] | None
) -> dict[str, Any]:
    """组合采购要求、供应商承诺和合同约定，供同屏核验。"""
    return {"procurement": procurement_item, "response": response_item, "contract": contract_item}


def check_commitment_transfer(
    *, context: ToolContext, alignments: list[dict[str, Any]]
) -> dict[str, Any]:
    """筛出未找到合同承接证据的三方对照事项。"""
    missing = [item for item in alignments if item.get("status") == "evidence_insufficient"]
    return {"missing_or_uncertain": missing, "count": len(missing)}
