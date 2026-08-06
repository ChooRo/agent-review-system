import json
import os
import threading
from pathlib import Path
from typing import Any


class JsonStore:
    """Small atomic JSON repository used only by backend services."""

    _lock = threading.RLock()  # ponytail: process-local lock; replace with DB transactions for multi-process deployment.

    def __init__(self, path: Path) -> None:
        self.path = path

    def read(self) -> dict[str, Any]:
        with self._lock:
            if not self.path.exists():
                return {"version": 1, "items": []}
            return json.loads(self.path.read_text(encoding="utf-8"))

    def write(self, value: dict[str, Any]) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temporary, self.path)
