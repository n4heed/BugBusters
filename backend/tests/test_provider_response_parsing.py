"""Regression coverage for OpenAICompatibleProvider's response parsing.

Covers a real compatibility gap found while wiring up Gemini via its
OpenAI-compatible endpoint: Gemini sometimes wraps its JSON reply in a
```json ... ``` markdown code fence despite being instructed to return raw
JSON. No network calls or credentials are used here — httpx.Client.post is
monkeypatched.
"""
from __future__ import annotations

import pytest

from app.ai.provider import OpenAICompatibleProvider, ProviderResponseError, _strip_markdown_fence
from app.ai.schemas import EvidenceAlertRef, EvidencePackage


def _evidence() -> EvidencePackage:
    return EvidencePackage(
        candidate_group_id="TEST-GROUP",
        alerts=[
            EvidenceAlertRef(
                id="A1", timestamp="2026-01-01T00:00:00Z", service="checkout-svc", signal="CPU high", severity="critical"
            )
        ],
        dependency_edges=[],
        historical_cases=[],
    )


def _valid_response_json() -> str:
    return (
        '{"incident_id": "TEST-GROUP", "probable_origin": "checkout-svc", "affected_services": ["checkout-svc"], '
        '"supporting_evidence_ids": ["A1"], "historical_case_ids": [], "hypotheses": ["CPU exhaustion"], '
        '"contradictory_evidence": [], "missing_evidence": [], "uncertainty_reason": null, '
        '"recommended_next_steps": ["Check autoscaling"]}'
    )


@pytest.mark.parametrize(
    "wrapped, expected",
    [
        ("```json\n{\"a\": 1}\n```", '{"a": 1}'),
        ("```\n{\"a\": 1}\n```", '{"a": 1}'),
        ('{"a": 1}', '{"a": 1}'),
        ("  ```json\n{\"a\": 1}\n```  ", '{"a": 1}'),
    ],
)
def test_strip_markdown_fence(wrapped, expected):
    assert _strip_markdown_fence(wrapped) == expected


class _FakeHttpResponse:
    def __init__(self, content: str):
        self._content = content

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {
            "choices": [{"message": {"content": self._content}}],
            "usage": {"prompt_tokens": 42, "completion_tokens": 17},
        }


class _FakeHttpClient:
    def __init__(self, content: str, **kwargs):
        self._content = content

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, *args, **kwargs):
        return _FakeHttpResponse(self._content)


def test_invoke_investigation_parses_markdown_fenced_response(monkeypatch):
    fenced = f"```json\n{_valid_response_json()}\n```"
    monkeypatch.setattr("app.ai.provider.httpx.Client", lambda **kwargs: _FakeHttpClient(fenced))
    monkeypatch.setenv("LLM_API_KEY", "test-key")

    provider = OpenAICompatibleProvider("openai-compatible", "gemini-3.5-flash-lite", "https://example.invalid/v1")
    response, usage = provider.invoke_investigation(_evidence(), max_output_tokens=100, timeout_seconds=5)

    assert response.incident_id == "TEST-GROUP"
    assert usage["input_tokens"] == 42
    assert usage["output_tokens"] == 17


def test_invoke_investigation_still_rejects_genuinely_malformed_response(monkeypatch):
    monkeypatch.setattr("app.ai.provider.httpx.Client", lambda **kwargs: _FakeHttpClient("not json at all"))
    monkeypatch.setenv("LLM_API_KEY", "test-key")

    provider = OpenAICompatibleProvider("openai-compatible", "gemini-3.5-flash-lite", "https://example.invalid/v1")
    with pytest.raises(ProviderResponseError):
        provider.invoke_investigation(_evidence(), max_output_tokens=100, timeout_seconds=5)
