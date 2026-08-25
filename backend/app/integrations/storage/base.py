from __future__ import annotations

from pathlib import Path
from typing import BinaryIO, Protocol


class Storage(Protocol):
    """统一文件存储最小契约；当前仅由 LocalStorage 实现。"""

    def upload(self, key: str, source: BinaryIO | bytes) -> str: ...

    def read(self, key: str) -> bytes: ...

    def delete(self, key: str) -> None: ...

    def download_url(self, key: str) -> str: ...


def safe_path(root: Path, key: str) -> Path:
    """把对象键限制在存储根目录内，拒绝路径穿越。"""
    path = (root / key).resolve()
    if path != root.resolve() and root.resolve() not in path.parents:
        raise ValueError("storage key escapes the storage root")
    return path
