"""文档画像读取 Tool。"""

from typing import Any

from ..schemas import ToolContext


def get_document_profile(*, context: ToolContext, profile: dict[str, Any]) -> dict[str, Any]:
    """作用：读取当前授权文档的全局画像。
    输入：context 为调用上下文，profile 为文档画像。
    输出：文档画像的浅拷贝。
    逻辑：复制输入字典，避免调用方直接修改工作流中的原对象。
    """
    return dict(profile)
