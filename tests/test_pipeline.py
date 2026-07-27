from unittest.mock import patch

from app.nim_client import CompletionResult
from app.pipeline import run_pipeline


def _fake_completion(model, system_prompt, user_prompt, max_tokens=800, temperature=0.2):
    # Deterministic fake so tests don't need a real NIM API key/network call.
    return CompletionResult(
        text=f"[{model}] response to: {user_prompt[:30]}",
        input_tokens=len(user_prompt.split()),
        output_tokens=10,
        latency_ms=5.0,
        model=model,
    )


@patch("app.pipeline.call_nim", side_effect=_fake_completion)
def test_pipeline_runs_all_four_steps(mock_call):
    result = run_pipeline("trace123", "What is the refund window?", [])
    step_names = [s["step"] for s in result["steps"]]
    assert step_names == ["plan", "retrieve", "reason", "format"]
    assert all(s["status"] == "ok" for s in result["steps"])


@patch("app.pipeline.call_nim", side_effect=_fake_completion)
def test_pipeline_uses_tiered_models(mock_call):
    run_pipeline("trace123", "Compare two policies", ["doc a", "doc b"])
    called_models = [c.kwargs["model"] for c in mock_call.call_args_list]
    # plan, retrieve, format -> small; reason -> large (see docs/design.md tiering table)
    assert called_models == [
        "test/small-model",
        "test/small-model",
        "test/large-model",
        "test/small-model",
    ]


@patch("app.pipeline.call_nim", side_effect=_fake_completion)
def test_token_report_sums_per_step(mock_call):
    result = run_pipeline("trace123", "hello world query", [])
    report = result["token_report"]
    summed_in = sum(v["input_tokens"] for v in report["per_step"].values())
    summed_out = sum(v["output_tokens"] for v in report["per_step"].values())
    assert report["total_input_tokens"] == summed_in
    assert report["total_output_tokens"] == summed_out


@patch("app.pipeline.call_nim", side_effect=_fake_completion)
def test_raw_retrieved_content_not_sent_to_reason_step(mock_call):
    """
    Core memory.md claim: REASON should see the SUMMARY, not the raw context documents
    verbatim in full. This test guards against someone accidentally reintroducing the
    100K-token regression by concatenating raw history back in.
    """
    huge_doc = "REDACTED_RAW_MARKER " * 500
    run_pipeline("trace123", "summarize this", [huge_doc])
    reason_call = mock_call.call_args_list[2]  # 3rd call = reason step
    reason_prompt = reason_call.kwargs["user_prompt"]
    # the raw 500-repeat marker blob should NOT appear wholesale in the reason prompt
    assert reason_prompt.count("REDACTED_RAW_MARKER") < 500
