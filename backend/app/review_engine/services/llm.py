"""OpenAI-compatible LLM calls with JSON output validation."""

from __future__ import annotations

import json
import re
import threading
import time
from typing import Any

import httpx

from .runtime import RunStore, write_json


class LLMService:
    def __init__(self, config: dict[str, Any], store: RunStore):
        api_url = str(config.get("api_url") or "").rstrip("/")
        self.api_key = str(config.get("api_key") or "")
        self.model = str(config.get("model") or "")
        if not api_url or not self.api_key or not self.model:
            raise ValueError("LLM requires api_url, api_key, and model")
        self.store = store
        self.max_retries = int(config.get("max_retries", 0))
        self._trace_lock = threading.Lock()
        self.client = httpx.Client(base_url=api_url, timeout=float(config.get("timeout_seconds", 120)))

    def json_call(self, step: str, system_prompt: str, payload: dict[str, Any]) -> dict[str, Any]:
        trace_dir = self.store.run_dir / "llm_traces"
        trace_dir.mkdir(parents=True, exist_ok=True)
        request_body = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
        }
        with self._trace_lock:
            trace_no = len(list(trace_dir.glob(f"{step}_*.json"))) + 1
            trace_path = trace_dir / f"{step}_{trace_no:03d}.json"
            write_json(trace_path, {"request": request_body, "response": None})
        try:
            for attempt in range(self.max_retries + 1):
                try:
                    response = self.client.post(
                        "/chat/completions",
                        json={**request_body, "response_format": {"type": "json_object"}},
                        headers={"Authorization": f"Bearer {self.api_key}"},
                    )
                    response.raise_for_status()
                    break
                except httpx.TimeoutException:
                    if attempt >= self.max_retries:
                        raise
                except httpx.HTTPStatusError as exc:
                    if attempt >= self.max_retries or (exc.response.status_code != 429 and exc.response.status_code < 500):
                        raise
                time.sleep(min(2**attempt, 2))
            content = response.json()["choices"][0]["message"].get("content") or ""
            result = parse_json_object(content)
            write_json(trace_path, {"request": request_body, "response": content})
            return result
        except Exception as exc:
            write_json(trace_path, {"request": request_body, "error": str(exc)})
            raise RuntimeError(f"LLM call failed; trace: {trace_path}") from exc


def parse_json_object(content: Any) -> dict[str, Any]:
    if isinstance(content, dict):
        return content
    if not isinstance(content, str):
        raise ValueError("LLM response is not text or a JSON object")
    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", content, re.I | re.S)
    text = fenced.group(1) if fenced else content
    start = text.find("{")
    if start < 0:
        raise ValueError("LLM response contains no JSON object")
    value, _ = json.JSONDecoder().raw_decode(text[start:])
    if not isinstance(value, dict):
        raise ValueError("LLM response JSON is not an object")
    return value
