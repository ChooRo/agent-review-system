import json

import httpx
import pytest

from app.review_engine.services.llm import LLMService
from app.review_engine.services.runtime import RunStore


def test_live_llm_uses_openai_compatible_httpx_endpoint(tmp_path, monkeypatch) -> None:
    calls = []

    class Client:
        def __init__(self, **_kwargs): pass
        def post(self, path, json=None, headers=None):
            calls.append((path, json, headers))
            return httpx.Response(200, request=httpx.Request("POST", "https://example.test/v1/chat/completions"), json={"choices": [{"message": {"content": '{"answer":"ok"}'}}]})

    monkeypatch.setattr("app.review_engine.services.llm.httpx.Client", Client)
    result = LLMService({"api_url": "https://example.test/v1", "api_key": "test-key", "model": "test-model"}, "live", RunStore(tmp_path)).json_call("smoke", "return JSON", {"input": 1}, {})
    assert result == {"answer": "ok"}
    assert calls[0][0] == "/chat/completions"
    assert calls[0][1]["model"] == "test-model"
    assert calls[0][2]["Authorization"] == "Bearer test-key"


@pytest.mark.parametrize("failure", ["timeout", 429, 500])
def test_live_llm_retries_only_transient_failures(tmp_path, monkeypatch, failure) -> None:
    calls = []

    class Client:
        def __init__(self, **_kwargs): pass
        def post(self, path, json=None, headers=None):
            request = httpx.Request("POST", "https://example.test/v1/chat/completions")
            calls.append(path)
            if len(calls) == 1:
                if failure == "timeout":
                    raise httpx.ReadTimeout("slow", request=request)
                return httpx.Response(failure, request=request)
            return httpx.Response(200, request=request, json={"choices": [{"message": {"content": '{"answer":"ok"}'}}]})

    monkeypatch.setattr("app.review_engine.services.llm.httpx.Client", Client)
    monkeypatch.setattr("app.review_engine.services.llm.time.sleep", lambda _seconds: None)
    service = LLMService({"api_url": "https://example.test/v1", "api_key": "test-key", "model": "test-model", "max_retries": 1}, "live", RunStore(tmp_path))

    assert service.json_call("retry", "return JSON", {}, {}) == {"answer": "ok"}
    assert len(calls) == 2


@pytest.mark.parametrize("response", [httpx.Response(400), httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]})])
def test_live_llm_does_not_retry_permanent_or_json_failures(tmp_path, monkeypatch, response) -> None:
    calls = []

    class Client:
        def __init__(self, **_kwargs): pass
        def post(self, path, json=None, headers=None):
            calls.append(path)
            response.request = httpx.Request("POST", "https://example.test/v1/chat/completions")
            return response

    monkeypatch.setattr("app.review_engine.services.llm.httpx.Client", Client)
    service = LLMService({"api_url": "https://example.test/v1", "api_key": "test-key", "model": "test-model", "max_retries": 1}, "live", RunStore(tmp_path))

    with pytest.raises(RuntimeError):
        service.json_call("no_retry", "return JSON", {}, {})
    assert len(calls) == 1
