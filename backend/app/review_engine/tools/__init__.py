"""审查 Agent 可用 Tool 的统一注册入口。"""

from .common import document, ledger, retrieval, validation
from .legal import rules
from .procurement import contract, rectification, response, review
from .registry import ToolRegistry
from .schemas import ToolContext, ToolResult


COMMON = {
    "get_document_profile", "query_ledger_items", "search_blocks", "get_block_context",
    "locate_source", "search_rules", "search_legal_units", "check_rule_applicability", "run_deterministic_checks",
    "validate_evidence",
}
AGENT_TOOL_ALLOWLISTS = {
    "workflow": COMMON,
    "procurement_review_agent": COMMON | {"get_procurement_topic_view", "check_required_elements", "evaluate_rule_coverage", "compare_procurement_sections", "compare_document_versions", "map_finding_to_new_version"},
    "response_review_agent": COMMON | {"get_procurement_requirement", "search_response_evidence"},
    "contract_review_agent": COMMON | {"get_three_party_item", "check_commitment_transfer", "compare_document_versions", "map_finding_to_new_version"},
}


def build_registry(event=None) -> ToolRegistry:
    """作用：创建审核 Agent 使用的默认工具注册表。
    输入：event 为可选审计事件回调。
    输出：配置好工具实现和 Agent 白名单的 ToolRegistry。
    逻辑：扫描业务工具模块中的公开函数，再连同白名单和回调构造注册表。
    """
    modules = (
        document, ledger, retrieval, rules, validation,
        review, response, contract, rectification,
    )
    tools = {
        name: value for module in modules for name, value in vars(module).items()
        if callable(value) and not name.startswith("_") and getattr(value, "__module__", "") == module.__name__
    }
    return ToolRegistry(tools, AGENT_TOOL_ALLOWLISTS, event=event)


__all__ = ["AGENT_TOOL_ALLOWLISTS", "ToolContext", "ToolResult", "ToolRegistry", "build_registry"]
