from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.nim_client import CompletionResult


def _fake_completion(model, system_prompt, user_prompt, max_tokens=800, temperature=0.2):
    return CompletionResult(
        text="mock answer", input_tokens=20, output_tokens=10, latency_ms=5.0, model=model
    )


client = TestClient(app)


def test_health_endpoint():
    resp = client.get("/v1/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_query_requires_api_key_when_configured():
    resp = client.post("/v1/query", json={"query": "hi"})
    assert resp.status_code == 401


@patch("app.pipeline.call_nim", side_effect=_fake_completion)
def test_query_succeeds_with_valid_key(mock_call):
    resp = client.post(
        "/v1/query",
        json={"query": "What is the refund policy?"},
        headers={"x-api-key": "test-client-key"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "answer" in body
    assert "trace_id" in body
    assert body["token_report"]["total_input_tokens"] > 0


def test_query_rejects_oversized_input():
    resp = client.post(
        "/v1/query",
        json={"query": "x" * 5000},
        headers={"x-api-key": "test-client-key"},
    )
    assert resp.status_code == 422


def test_error_response_has_no_stack_trace_leak():
    """Guards docs/rule.md Error handling rule: no internal detail beyond a trace_id."""
    resp = client.post("/v1/query", json={"query": ""}, headers={"x-api-key": "test-client-key"})
    assert resp.status_code == 422
    text = resp.text
    assert "Traceback" not in text
    assert "/home/" not in text
    assert "site-packages" not in text
