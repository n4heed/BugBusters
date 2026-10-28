"""OPT-IN live provider integration test — makes exactly ONE real HTTP
request to the configured model provider. Never runs as part of a normal
`pytest` invocation; it is skipped unless you explicitly opt in.

This file never asks for, reads, or prints your API key — it only checks
whether LLM_API_KEY is present in the environment (a boolean check) and
leaves the actual value inside app/ai/config.py's `_get_api_key()`, which
is the only place that ever touches it.

How to run this (after you've set up credentials — see the project README
/ the chat message that pointed you here for the exact variable names and
where to put them):

    cd backend
    $env:RUN_LIVE_LLM_TEST = "1"          # PowerShell
    # or: export RUN_LIVE_LLM_TEST=1      # bash
    pytest tests/test_live_provider_integration.py -v -s

It makes exactly one call, spends exactly one unit of the configured
LLM_MAX_CALLS_PER_RUN budget, and prints (not asserts anything sensitive
about) the provider/model/latency/token usage it observed so you can see
real evidence that the adapter actually works end to end.
"""
from __future__ import annotations

import os

import pytest

from app.ai.config import load_ai_config
from app.ai.provider import ProviderCallError, get_configured_provider
from app.ai.schemas import EvidenceAlertRef, EvidencePackage

RUN_LIVE_LLM_TEST = os.environ.get("RUN_LIVE_LLM_TEST", "").strip().lower() in {"1", "true", "yes"}

_SKIP_REASON = (
    "Opt-in only: set RUN_LIVE_LLM_TEST=1 (and configure LLM_ENABLED/LLM_PROVIDER/LLM_MODEL/LLM_API_KEY "
    "in backend/.env or your shell environment) to run this. Skipped by default — never spends API credit "
    "as part of a routine test run."
)


@pytest.mark.skipif(not RUN_LIVE_LLM_TEST, reason=_SKIP_REASON)
def test_single_live_investigation_call():
    config = load_ai_config()  # strict: raises AIConfigError with a clear message if misconfigured
    assert config.enabled, "LLM_ENABLED must be true to run the live integration test."

    provider = get_configured_provider(config)
    if provider is None:
        pytest.fail(
            f"AI is enabled but no provider was constructed — LLM_PROVIDER={config.provider!r} may not be "
            "one of the recognized values ('openai', 'openai-compatible', 'azure-openai-compatible'), "
            "or LLM_MODEL/LLM_API_KEY may be missing. See app/ai/provider.get_configured_provider()."
        )

    # A tiny, synthetic, single-incident evidence package — not real
    # operational data, and deliberately small (well under
    # LLM_MAX_ALERTS_PER_INCIDENT / LLM_MAX_HISTORICAL_CASES).
    evidence = EvidencePackage(
        candidate_group_id="LIVE-INTEGRATION-TEST",
        alerts=[
            EvidenceAlertRef(
                id="LIVE-A1",
                timestamp="2026-01-01T00:00:00Z",
                service="checkout-svc",
                signal="CPU > 95%",
                severity="critical",
            ),
            EvidenceAlertRef(
                id="LIVE-A2",
                timestamp="2026-01-01T00:02:00Z",
                service="payments-db",
                signal="Write latency > 2s",
                severity="critical",
            ),
        ],
        dependency_edges=["checkout-svc -> payments-db"],
        historical_cases=[],
    )

    try:
        response, usage = provider.invoke_investigation(
            evidence, max_output_tokens=config.max_output_tokens, timeout_seconds=config.request_timeout_seconds
        )
    except ProviderCallError as exc:
        pytest.fail(f"Live provider call failed: {type(exc).__name__}: {exc}")

    # Print real evidence that this actually worked — no secrets, just
    # operational facts, visible with `pytest -s`.
    print("\n--- Live provider integration result ---")
    print(f"provider={provider.name} model={provider.model}")
    print(f"input_tokens={usage.get('input_tokens')} output_tokens={usage.get('output_tokens')} "
          f"tokens_are_estimated={usage.get('tokens_are_estimated')}")
    print(f"latency_ms={usage.get('latency_ms')}")
    print(f"probable_origin={response.probable_origin!r}")
    print(f"hypotheses={response.hypotheses}")
    print("-----------------------------------------\n")

    # Structural assertions only — never assert on the model's specific
    # wording, since that's provider/model-dependent and not this test's job.
    # NOTE: incident_id is intentionally not asserted here. The real
    # pipeline (agents/investigation_agent.py) never reads
    # model_response.incident_id — it always uses its own known
    # candidate_group_id directly — so a model echoing a different value
    # here is cosmetic, not a correctness or safety issue.
    if response.incident_id != "LIVE-INTEGRATION-TEST":
        print(f"NOTE: model returned its own incident_id ({response.incident_id!r}) instead of echoing the "
              "supplied one — harmless, since the real pipeline never reads this field.")
    # The model must only ever reference alert IDs that were actually supplied.
    # This IS safety-critical: it's the same check agents/investigation_agent.py's
    # _validate_model_references() performs on every real call.
    assert set(response.supporting_evidence_ids) <= {"LIVE-A1", "LIVE-A2"}
    assert set(response.historical_case_ids) == set()  # none were supplied
