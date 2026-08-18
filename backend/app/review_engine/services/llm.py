"""OpenAI-compatible LLM calls with JSON output validation."""

from __future__ import annotations

import json
import random
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
        self.timeout_seconds = int(config.get("timeout_seconds", 120))
        self._trace_lock = threading.Lock()
        self.client = httpx.Client(base_url=api_url, timeout=float(self.timeout_seconds))

    def json_call(
        self,
        step: str,
        system_prompt: str,
        payload: dict[str, Any],
        *,
        max_tokens: int | None = None,
        stream: bool = False,
        timeout_seconds: int | None = None,
        idle_timeout_seconds: int | None = None,
        total_timeout_seconds: int | None = None,
        max_retries: int | None = None,
        trace_label: str | None = None,
        trace_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        trace_dir = self.store.run_dir / "llm_traces"
        trace_dir.mkdir(parents=True, exist_ok=True)
        request_body = {
            "model": self.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
        }
        if max_tokens is not None:
            request_body["max_tokens"] = int(max_tokens)
        if stream:
            request_body["stream"] = True
        label = re.sub(r"[^A-Za-z0-9_-]+", "_", str(trace_label or "")).strip("_")
        prefix = f"{step}_{label}" if label else step
        with self._trace_lock:
            trace_no = len(list(trace_dir.glob(f"{prefix}_*.json"))) + 1
            suffix = f"call{trace_no:03d}" if label else f"{trace_no:03d}"
            trace_path = trace_dir / f"{prefix}_{suffix}.json"
            write_json(trace_path, {"step": step, "request": request_body, "context": trace_context or {}, "response": None})
        attempts: list[dict[str, Any]] = []
        started = time.monotonic()
        retries = self.max_retries if max_retries is None else int(max_retries)
        request_timeout = int(timeout_seconds or self.timeout_seconds)
        idle_timeout = int(idle_timeout_seconds or request_timeout)
        total_timeout = int(total_timeout_seconds or request_timeout)
        try:
            for attempt in range(retries + 1):
                try:
                    if stream:
                        content, finish_reason = self._stream_content(request_body, idle_timeout, total_timeout)
                    else:
                        response = self.client.post(
                            "/chat/completions",
                            json=request_body,
                            headers={"Authorization": f"Bearer {self.api_key}"},
                            timeout=request_timeout,
                        )
                        response.raise_for_status()
                        choice = response.json()["choices"][0]
                        content = choice["message"].get("content") or ""
                        finish_reason = choice.get("finish_reason")
                    attempts.append({
                        "attempt": attempt + 1,
                        "status": "completed",
                        "received_characters": len(content),
                        "finish_reason": finish_reason,
                    })
                    break
                except httpx.HTTPStatusError as exc:
                    attempts.append({
                        "attempt": attempt + 1,
                        "status": "failed",
                        "error_type": type(exc).__name__,
                        "status_code": exc.response.status_code,
                    })
                    if attempt >= retries or (exc.response.status_code != 429 and exc.response.status_code < 500):
                        raise
                except httpx.TransportError as exc:
                    attempts.append({
                        "attempt": attempt + 1,
                        "status": "failed",
                        "error_type": type(exc).__name__,
                    })
                    if attempt >= retries:
                        raise
                time.sleep(min(2**attempt, 8) + random.random())
            result = parse_json_object(content)
            write_json(trace_path, {
                "step": step,
                "request": request_body,
                "context": trace_context or {},
                "response": content,
                "attempts": attempts,
                "duration_seconds": round(time.monotonic() - started, 3),
            })
            return result
        except Exception as exc:
            write_json(trace_path, {
                "step": step,
                "request": request_body,
                "context": trace_context or {},
                "error": str(exc),
                "attempts": attempts,
                "duration_seconds": round(time.monotonic() - started, 3),
            })
            raise RuntimeError(f"LLM call failed; trace: {trace_path}") from exc

    def _stream_content(
        self, request_body: dict[str, Any], idle_timeout: int, total_timeout: int
    ) -> tuple[str, str | None]:
        """Collect OpenAI-compatible SSE chunks; incomplete streams are never parsed or admitted."""
        chunks: list[str] = []
        finish_reason = None
        started = time.monotonic()
        timeout = httpx.Timeout(idle_timeout, connect=min(30, idle_timeout))
        with self.client.stream(
            "POST",
            "/chat/completions",
            json=request_body,
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=timeout,
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if time.monotonic() - started > total_timeout:
                    raise httpx.ReadTimeout(
                        f"stream exceeded total timeout of {total_timeout}s",
                        request=response.request,
                    )
                text = line.decode() if isinstance(line, bytes) else str(line)
                text = text.strip()
                if not text or text.startswith(":"):
                    continue
                if text.startswith("data:"):
                    text = text[5:].strip()
                if text == "[DONE]":
                    break
                event = json.loads(text)
                choice = (event.get("choices") or [{}])[0]
                if choice.get("finish_reason") is not None:
                    finish_reason = str(choice["finish_reason"])
                content = (choice.get("delta") or {}).get("content")
                if content is None:
                    content = (choice.get("message") or {}).get("content")
                if content:
                    chunks.append(str(content))
        return "".join(chunks), finish_reason


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
