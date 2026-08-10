"""OpenAI-compatible大模型调用和JSON输出校验。"""

from __future__ import annotations

import json
import re
import threading
import time
from typing import Any

import httpx

from .runtime import RunStore, write_json


class LLMService:
    """统一调用模型；mock模式生成确定性结果，便于无密钥调试全流程。"""

    def __init__(self, config: dict[str, Any], mode: str, store: RunStore):
        self.mode = mode
        self.store = store
        self.model = str(config.get("model") or "mock-model")
        self.max_retries = int(config.get("max_retries", 0))
        self._trace_lock = threading.Lock()
        self.client: httpx.Client | None = None
        if mode == "live":
            api_url = str(config.get("api_url") or "").rstrip("/")
            api_key = str(config.get("api_key") or "")
            if not api_url or not api_key or not config.get("model"):
                raise ValueError("live模式必须配置api_url、api_key和model")
            self.client = httpx.Client(base_url=api_url, timeout=float(config.get("timeout_seconds", 120)))
            self.api_key = api_key

    def json_call(
        self,
        step: str,
        system_prompt: str,
        payload: dict[str, Any],
        mock_result: dict[str, Any],
    ) -> dict[str, Any]:
        """调用模型并保存脱敏请求与原始返回；mock模式直接返回测试结果。"""
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
            write_json(trace_path, {"mode": self.mode, "request": request_body, "response": None})
        if self.mode == "mock":
            write_json(trace_path, {"mode": "mock", "request": request_body, "response": mock_result})
            return mock_result

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
                    status = exc.response.status_code
                    if attempt >= self.max_retries or (status != 429 and status < 500):
                        raise
                time.sleep(min(2**attempt, 2))
            content = response.json()["choices"][0]["message"].get("content") or ""
            result = parse_json_object(content)
            write_json(trace_path, {"mode": "live", "request": request_body, "response": content})
            return result
        except Exception as exc:
            write_json(trace_path, {"mode": "live", "request": request_body, "error": str(exc)})
            raise RuntimeError(f"LLM调用失败，追踪文件：{trace_path}") from exc


def parse_json_object(content: Any) -> dict[str, Any]:
    """解析模型JSON对象，兼容Markdown代码围栏。"""
    if isinstance(content, dict):
        return content
    if not isinstance(content, str):
        raise ValueError("模型返回不是文本或JSON对象")
    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", content, re.I | re.S)
    text = fenced.group(1) if fenced else content
    start = text.find("{")
    if start < 0:
        raise ValueError("模型返回中没有JSON对象")
    value, _ = json.JSONDecoder().raw_decode(text[start:])
    if not isinstance(value, dict):
        raise ValueError("模型返回JSON不是对象")
    return value
