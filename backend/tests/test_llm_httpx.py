import json

import httpx
import pytest

from app.review_engine.services.llm import LLMService
from app.review_engine.services.runtime import RunStore


def test_llm_uses_openai_compatible_httpx_endpoint(tmp_path, monkeypatch) -> None:
    calls = []

    class Client:
        def __init__(self, **_kwargs): pass
        def post(self, path, json=None, headers=None, timeout=None):
            calls.append((path, json, headers))
            return httpx.Response(200, request=httpx.Request("POST", "https://example.test/v1/chat/completions"), json={"choices": [{"message": {"content": '{"answer":"ok"}'}, "finish_reason": "stop"}]})

    monkeypatch.setattr("app.review_engine.services.llm.httpx.Client", Client)
    result = LLMService({"api_url": "https://example.test/v1", "api_key": "test-key", "model": "test-model"}, RunStore(tmp_path)).json_call("smoke", "return JSON", {"input": 1})
    assert result == {"answer": "ok"}
    assert calls[0][0] == "/chat/completions"
    assert calls[0][1]["model"] == "test-model"
    assert calls[0][2]["Authorization"] == "Bearer test-key"
    trace = json.loads(next((tmp_path / "llm_traces").glob("smoke_*.json")).read_text(encoding="utf-8"))
    assert "mode" not in trace
    assert trace["attempts"][0]["finish_reason"] == "stop"


@pytest.mark.parametrize("failure", ["timeout", "disconnect", 429, 500])
def test_llm_retries_only_transient_failures(tmp_path, monkeypatch, failure) -> None:
    calls = []

    class Client:
        def __init__(self, **_kwargs): pass
        def post(self, path, json=None, headers=None, timeout=None):
            request = httpx.Request("POST", "https://example.test/v1/chat/completions")
            calls.append(path)
            if len(calls) == 1:
                if failure == "timeout":
                    raise httpx.ReadTimeout("slow", request=request)
                if failure == "disconnect":
                    raise httpx.RemoteProtocolError("server disconnected", request=request)
                return httpx.Response(failure, request=request)
            return httpx.Response(200, request=request, json={"choices": [{"message": {"content": '{"answer":"ok"}'}}]})

    monkeypatch.setattr("app.review_engine.services.llm.httpx.Client", Client)
    monkeypatch.setattr("app.review_engine.services.llm.time.sleep", lambda _seconds: None)
    service = LLMService({"api_url": "https://example.test/v1", "api_key": "test-key", "model": "test-model", "max_retries": 1}, RunStore(tmp_path))

    assert service.json_call("retry", "return JSON", {}) == {"answer": "ok"}
    assert len(calls) == 2


@pytest.mark.parametrize("response", [
    httpx.Response(400),
    httpx.Response(200, json={"choices": [{"message": {"content": '{"answer":'}, "finish_reason": "length"}]}),
])
def test_llm_does_not_retry_permanent_or_json_failures(tmp_path, monkeypatch, response) -> None:
    calls = []

    class Client:
        def __init__(self, **_kwargs): pass
        def post(self, path, json=None, headers=None, timeout=None):
            calls.append(path)
            response.request = httpx.Request("POST", "https://example.test/v1/chat/completions")
            return response

    monkeypatch.setattr("app.review_engine.services.llm.httpx.Client", Client)
    service = LLMService({"api_url": "https://example.test/v1", "api_key": "test-key", "model": "test-model", "max_retries": 1}, RunStore(tmp_path))

    with pytest.raises(RuntimeError):
        service.json_call("no_retry", "return JSON", {})
    assert len(calls) == 1
    if response.status_code == 200:
        trace = json.loads(next((tmp_path / "llm_traces").glob("no_retry_*.json")).read_text(encoding="utf-8"))
        assert trace["attempts"][0]["finish_reason"] == "length"


def test_llm_streams_json_with_output_limit_and_batch_trace(tmp_path, monkeypatch) -> None:
    calls = []

    class StreamResponse:
        request = httpx.Request("POST", "https://example.test/v1/chat/completions")
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def raise_for_status(self): return None
        @staticmethod
        def iter_lines():
            return iter([
                'data: {"choices":[{"delta":{"content":"{\\"answer\\":"}}]}',
                'data: {"choices":[{"delta":{"content":"\\"ok\\"}"}}]}',
                'data: {"choices":[{"delta":{},"finish_reason":"length"}]}',
                "data: [DONE]",
            ])

    class Client:
        def __init__(self, **_kwargs): pass
        def stream(self, method, path, json=None, headers=None, timeout=None):
            calls.append((method, path, json, timeout))
            return StreamResponse()

    monkeypatch.setattr("app.review_engine.services.llm.httpx.Client", Client)
    result = LLMService(
        {"api_url": "https://example.test/v1", "api_key": "test-key", "model": "test-model"},
        RunStore(tmp_path),
    ).json_call(
        "extract_candidates", "return JSON", {}, stream=True, max_tokens=3000,
        trace_label="procurement_b009", trace_context={"batch_no": 9},
    )
    assert result == {"answer": "ok"}
    assert calls[0][2]["stream"] is True
    assert calls[0][2]["max_tokens"] == 3000
    trace_path = next((tmp_path / "llm_traces").glob("extract_candidates_procurement_b009_call*.json"))
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    assert trace["context"]["batch_no"] == 9
    assert trace["attempts"][0]["received_characters"] == len('{"answer":"ok"}')
    assert trace["attempts"][0]["finish_reason"] == "length"
