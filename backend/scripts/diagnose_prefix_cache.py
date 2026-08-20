"""探测 OpenAI 兼容端点是否存在可观测的前缀缓存复用。"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.review_engine.services.procurement.workflow import (
    PROCUREMENT_EXTRACTION_CONTRACT,
    load_formal_skill,
)
from app.review_engine.settings import load_settings


def cached_tokens(usage: dict[str, Any]) -> int | None:
    details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}
    value = details.get("cached_tokens", usage.get("cached_tokens"))
    return int(value) if value is not None else None


def call(client: httpx.Client, body: dict[str, Any], api_key: str) -> dict[str, Any]:
    started = time.perf_counter()
    first_token: float | None = None
    usage: dict[str, Any] = {}
    cache_headers: dict[str, str] = {}
    with client.stream(
        "POST", "/chat/completions", json=body,
        headers={"Authorization": f"Bearer {api_key}"},
    ) as response:
        response.raise_for_status()
        cache_headers = {
            key: value for key, value in response.headers.items()
            if "cache" in key.lower()
        }
        for line in response.iter_lines():
            text = line.decode() if isinstance(line, bytes) else str(line)
            text = text.strip()
            if not text or text.startswith(":"):
                continue
            if text.startswith("data:"):
                text = text[5:].strip()
            if text == "[DONE]":
                break
            event = json.loads(text)
            if event.get("usage"):
                usage = event["usage"]
            choice = (event.get("choices") or [{}])[0]
            delta = choice.get("delta") or choice.get("message") or {}
            if first_token is None and (delta.get("content") or delta.get("reasoning_content")):
                first_token = time.perf_counter() - started
    return {
        "ttft_seconds": round(first_token, 3) if first_token is not None else None,
        "total_seconds": round(time.perf_counter() - started, 3),
        "prompt_tokens": usage.get("prompt_tokens"),
        "cached_tokens": cached_tokens(usage),
        "cache_headers": cache_headers,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("review_config.json"))
    parser.add_argument("--calls", type=int, default=4)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    settings = load_settings(args.config.resolve())
    llm = settings["llm"]
    skill_dir = Path(__file__).parents[1] / "app/review_engine/skills/understand-procurement-document"
    prefix = load_formal_skill(skill_dir, include_references=False) + PROCUREMENT_EXTRACTION_CONTRACT
    if args.dry_run:
        print(json.dumps({"ready": True, "model": llm["model"], "calls": args.calls, "prefix_characters": len(prefix)}, ensure_ascii=False))
        return

    body = {
        "model": llm["model"], "temperature": 0, "max_tokens": 16,
        "stream": True, "stream_options": {"include_usage": True},
        "messages": [{"role": "system", "content": prefix}, {"role": "user", "content": ""}],
    }
    results = []
    with httpx.Client(base_url=str(llm["api_url"]).rstrip("/"), timeout=120) as client:
        for index in range(args.calls):
            body["messages"][1]["content"] = f"前缀缓存诊断请求 {index + 1}：只返回空candidate_items JSON。"
            results.append(call(client, body, str(llm["api_key"])))
            print(json.dumps({"call": index + 1, **results[-1]}, ensure_ascii=False), flush=True)

    observed = [item["cached_tokens"] for item in results if item["cached_tokens"] is not None]
    ttfts = [item["ttft_seconds"] for item in results if item["ttft_seconds"] is not None]
    status = "confirmed" if any(value > 0 for value in observed) else ("not_observed" if observed else "not_exposed")
    if status == "not_exposed" and len(ttfts) >= 3 and ttfts[0] > 0:
        status = "likely" if statistics.median(ttfts[1:]) <= ttfts[0] * 0.7 else "inconclusive"
    print(json.dumps({"status": status, "calls": results}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
