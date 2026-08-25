from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import BinaryIO

from .base import safe_path


class LocalStorage:
    """单机开发环境文件存储；不负责对象服务或跨进程同步。"""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).expanduser().resolve()

    def path(self, key: str) -> Path:
        return safe_path(self.root, key)

    def upload(self, key: str, source: BinaryIO | bytes) -> str:
        target = self.path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.uploading")
        try:
            with temporary.open("wb") as destination:
                if isinstance(source, bytes):
                    destination.write(source)
                else:
                    shutil.copyfileobj(source, destination)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return key

    def read(self, key: str) -> bytes:
        return self.path(key).read_bytes()

    def delete(self, key: str) -> None:
        self.path(key).unlink(missing_ok=True)

    def download_url(self, key: str) -> str:
        return self.path(key).as_uri()
