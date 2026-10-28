"""AI integration configuration — read from environment variables only.

Never hardcode a credential here. `LLM_ENABLED` defaults to false, so the
deterministic pipeline works with zero configuration. Validated once at
import time (process startup) so a malformed limit fails fast and loudly
rather than silently disabling a safeguard.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


class AIConfigError(ValueError):
    pass


def _load_dotenv_if_present() -> None:
    """Minimal, dependency-free .env loader. Only fills in variables not
    already present in the process environment — a real shell export or
    CI-injected secret always wins over the file. No value is ever logged.
    """
    env_path = Path(__file__).resolve().parent.parent.parent / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise AIConfigError(f"{name} must be an integer, got {raw!r}") from exc
    if value < 0:
        raise AIConfigError(f"{name} must be >= 0, got {value}")
    return value


@dataclass(frozen=True)
class AIConfig:
    enabled: bool
    provider: str
    model: str
    base_url: str | None
    api_key_configured: bool  # never the key itself
    max_calls_per_run: int
    max_alerts_per_incident: int
    max_historical_cases: int
    max_output_tokens: int
    max_retries: int
    request_timeout_seconds: float

    def summary(self) -> dict:
        """Safe-to-render startup/diagnostics summary — never includes the key."""
        return {
            "ai_enabled": self.enabled,
            "provider": self.provider or None,
            "model": self.model or None,
            "api_key_configured": self.api_key_configured,
            "max_calls_per_run": self.max_calls_per_run,
            "max_alerts_per_incident": self.max_alerts_per_incident,
            "max_historical_cases": self.max_historical_cases,
            "max_output_tokens": self.max_output_tokens,
            "max_retries": self.max_retries,
        }


def load_ai_config() -> AIConfig:
    _load_dotenv_if_present()
    provider = os.environ.get("LLM_PROVIDER", "").strip()
    model = os.environ.get("LLM_MODEL", "").strip()
    base_url = os.environ.get("LLM_BASE_URL", "").strip() or None
    api_key = os.environ.get("LLM_API_KEY", "").strip()
    enabled = _env_bool("LLM_ENABLED", False)

    max_calls_per_run = _env_int("LLM_MAX_CALLS_PER_RUN", 10)
    max_alerts_per_incident = _env_int("LLM_MAX_ALERTS_PER_INCIDENT", 12)
    max_historical_cases = _env_int("LLM_MAX_HISTORICAL_CASES", 5)
    max_output_tokens = _env_int("LLM_MAX_OUTPUT_TOKENS", 1000)
    max_retries = _env_int("LLM_MAX_RETRIES", 1)
    request_timeout_seconds = float(_env_int("LLM_REQUEST_TIMEOUT_SECONDS", 20))

    if enabled and not provider:
        raise AIConfigError("LLM_ENABLED=true requires LLM_PROVIDER to be set.")
    if enabled and not model:
        raise AIConfigError("LLM_ENABLED=true requires LLM_MODEL to be set.")
    if enabled and not api_key:
        raise AIConfigError("LLM_ENABLED=true requires LLM_API_KEY to be set.")

    return AIConfig(
        enabled=enabled,
        provider=provider,
        model=model,
        base_url=base_url,
        api_key_configured=bool(api_key),
        max_calls_per_run=max_calls_per_run,
        max_alerts_per_incident=max_alerts_per_incident,
        max_historical_cases=max_historical_cases,
        max_output_tokens=max_output_tokens,
        max_retries=max_retries,
        request_timeout_seconds=request_timeout_seconds,
    )


def load_ai_config_safe() -> AIConfig:
    """Like load_ai_config(), but never raises: an invalid AI configuration
    must not take down the whole application, since the deterministic
    pipeline is required to work regardless. Falls back to AI disabled and
    the caller is responsible for surfacing the error (see main.py startup
    logging).
    """
    try:
        return load_ai_config()
    except AIConfigError:
        return AIConfig(
            enabled=False,
            provider="",
            model="",
            base_url=None,
            api_key_configured=False,
            max_calls_per_run=10,
            max_alerts_per_incident=12,
            max_historical_cases=5,
            max_output_tokens=1000,
            max_retries=1,
            request_timeout_seconds=20.0,
        )


def _get_api_key() -> str | None:
    """Only called from inside the provider adapter at call time — never
    logged, never placed on the AIConfig object, never returned by any API
    response.
    """
    return os.environ.get("LLM_API_KEY", "").strip() or None
