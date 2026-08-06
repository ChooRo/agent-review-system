"""文档画像读取 Tool。"""

from typing import Any

from .schemas import ToolContext


def get_document_profile(*, context: ToolContext, profile: dict[str, Any]) -> dict[str, Any]:
    """返回当前授权文档的全局画像。输入 profile；输出画像副本。"""
    return dict(profile)
