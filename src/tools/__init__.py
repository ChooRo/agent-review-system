"""审查 Agent 可用 Tool 的统一注册入口。"""

from . import (
    contract_tools, document_tools, ledger_tools, procurement_tools, rectification_tools,
    response_tools, retrieval_tools, rule_tools, validation_tools,
)
from .registry import ToolRegistry
from .schemas import ToolContext, ToolResult


COMMON = {
    "get_document_profile", "query_ledger_items", "search_blocks", "get_block_context",
    "locate_source", "search_rules", "search_legal_units", "check_rule_applicability", "run_deterministic_checks",
    "validate_evidence",
}
AGENT_TOOL_ALLOWLISTS = {
    "workflow": COMMON,
    "procurement_review_agent": COMMON | {"get_procurement_topic_view", "check_required_elements", "compare_document_versions", "map_finding_to_new_version"},
    "response_review_agent": COMMON | {"get_procurement_requirement", "search_response_evidence"},
    "contract_review_agent": COMMON | {"get_three_party_item", "check_commitment_transfer", "compare_document_versions", "map_finding_to_new_version"},
}


def build_registry(event=None) -> ToolRegistry:
    """创建默认注册表。输入可选事件函数；输出带白名单的 ToolRegistry。"""
    modules = (
        document_tools, ledger_tools, retrieval_tools, rule_tools, validation_tools,
        procurement_tools, response_tools, contract_tools, rectification_tools,
    )
    tools = {
        name: value for module in modules for name, value in vars(module).items()
        if callable(value) and not name.startswith("_") and getattr(value, "__module__", "") == module.__name__
    }
    return ToolRegistry(tools, AGENT_TOOL_ALLOWLISTS, event=event)


__all__ = ["AGENT_TOOL_ALLOWLISTS", "ToolContext", "ToolResult", "ToolRegistry", "build_registry"]
