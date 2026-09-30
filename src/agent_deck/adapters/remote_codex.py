"""Host-side Codex collector clients used by the Docker daemon."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx

from agent_deck.adapters.codex_quota import CodexQuotaSnapshot
from agent_deck.adapters.codex_tokens import CodexTokenUsageSnapshot


def remote_codex_quota_reader(base_url: str, *, timeout_seconds: float = 15.0) -> Callable[[], CodexQuotaSnapshot]:
    """Return a reader that fetches a host-collected Codex quota snapshot."""
    url = base_url.rstrip("/") + "/codex/quota"
    default_timeout = timeout_seconds

    def read(*, timeout_seconds: float | None = None) -> CodexQuotaSnapshot:
        effective_timeout = timeout_seconds if timeout_seconds is not None else default_timeout
        response = httpx.get(url, timeout=effective_timeout)
        response.raise_for_status()
        body: Any = response.json()
        if not isinstance(body, dict) or body.get("ok") is not True:
            raise ValueError(str(body.get("error", "host Codex quota collector failed")) if isinstance(body, dict) else "invalid host Codex quota response")
        snapshot = body.get("snapshot")
        if not isinstance(snapshot, dict):
            raise ValueError("host Codex quota response did not include snapshot")
        return CodexQuotaSnapshot.model_validate(snapshot)

    return read


def remote_codex_token_usage_reader(base_url: str, *, timeout_seconds: float = 15.0) -> Callable[[], CodexTokenUsageSnapshot]:
    """Return a reader that fetches a host-collected Codex token snapshot."""
    url = base_url.rstrip("/") + "/codex/token-usage"
    default_timeout = timeout_seconds

    def read() -> CodexTokenUsageSnapshot:
        response = httpx.get(url, timeout=default_timeout)
        response.raise_for_status()
        body: Any = response.json()
        if isinstance(body, dict) and body.get("error"):
            raise ValueError(str(body["error"]))
        if not isinstance(body, dict) or body.get("ok") is not True:
            raise ValueError("host Codex token collector failed")
        snapshot = body.get("snapshot")
        if not isinstance(snapshot, dict):
            raise ValueError("host Codex token response did not include snapshot")
        return CodexTokenUsageSnapshot.model_validate(snapshot)

    return read
