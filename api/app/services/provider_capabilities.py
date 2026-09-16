"""Typed provider/model capability profiles + one shared request builder.

Every agent (Router/Extractor/Judge, text and legacy image paths) builds
chat-completions kwargs through :func:`build_chat_kwargs`, with the rules
resolved from the EFFECTIVE (endpoint, model) — so a native registry row
and a base-URL override (e.g. ``openai`` label + Groq URL) behave
identically. URL parsing uses exact host matching; no substring logic.

Capability states:
- verified rows below (with evidence notes),
- provider-label fallback (registry ``strict_json_schema`` flag),
- unknown endpoints: conservative behavior — strict-first is an OPTIMISTIC
  PROBE, not a support claim. Prompt-embedded schema + Pydantic validation
  always run regardless of tier.

Only EXPLICIT unsupported-parameter responses are ever remembered (see
``client`` capability memory); malformed output, generic HTTP 400s, and
truncation never change capability state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

# Bump when the tables/rules below change: it is fingerprinted, so old
# cached results invalidate instead of being reused under new rules.
COMPAT_POLICY_VERSION = "compat-v1"


@dataclass(frozen=True)
class CapabilityProfile:
    """What a request to one (endpoint, model) may contain."""

    source: str  # "verified:<evidence>" | "provider-default:<label>" | "unknown-conservative"
    strict_first: bool  # start at the json_schema tier (unknown = optimistic probe)
    omit_extra_body_reasoning: bool  # omit legacy flag before the first request
    reasoning_levels: tuple[str, ...]  # allowed reasoning_effort values, () = none known
    token_param: str  # "max_tokens" everywhere today; per-endpoint when one diverges


# Exact (endpoint, model) rows verified by measurement or current official
# docs (access dates in docs/reference/provider_compatibility.md). Never a
# substring/prefix match: unknown pairs fall through to provider defaults.
_VERIFIED_RULES: dict[tuple[str, str], CapabilityProfile] = {
    (
        "https://integrate.api.nvidia.com/v1",
        "openai/gpt-oss-20b",
    ): CapabilityProfile(
        source="verified:live-probe-2026-09-14(low accepted, 14s vs 3000-token stall)",
        strict_first=True,
        omit_extra_body_reasoning=False,
        reasoning_levels=("low", "medium", "high"),
        token_param="max_tokens",
    ),
    (
        "https://api.groq.com/openai/v1",
        "openai/gpt-oss-20b",
    ): CapabilityProfile(
        source="verified:groq-docs-strict+reasoning-2026-09-14 + live-400-extra_body-2026-09-14",
        strict_first=True,
        omit_extra_body_reasoning=True,
        reasoning_levels=("low", "medium", "high"),
        token_param="max_tokens",
    ),
}

# Exact host attribution (endpoint → family label for reporting only).
# Grants NO capabilities by itself; unknown hosts are fully conservative.
KNOWN_HOSTS: dict[str, str] = {
    "api.groq.com": "groq",
    "integrate.api.nvidia.com": "nvidia",
}


def endpoint_host(endpoint: str) -> str:
    """Lowercased hostname of an endpoint URL ("" when unparseable)."""
    try:
        return (urlparse(endpoint).hostname or "").lower()
    except Exception:
        return ""


def resolve_capabilities(
    *,
    endpoint: str,
    model: str,
    provider_label: str = "",
    strict_default: bool = True,
) -> CapabilityProfile:
    """Resolve the request profile for one effective (endpoint, model).

    Precedence: exact verified row → provider-label default → unknown
    conservative (strict-first optimistic probe, current request shape).
    """
    key = ((endpoint or "").rstrip("/"), model)
    verified = _VERIFIED_RULES.get(key)
    if verified is not None:
        return verified
    if provider_label:
        return CapabilityProfile(
            source=f"provider-default:{provider_label}",
            strict_first=bool(strict_default),
            omit_extra_body_reasoning=False,
            reasoning_levels=(),
            token_param="max_tokens",
        )
    return CapabilityProfile(
        source="unknown-conservative",
        strict_first=True,
        omit_extra_body_reasoning=False,
        reasoning_levels=(),
        token_param="max_tokens",
    )


def build_chat_kwargs(
    *,
    model: str,
    messages: list[dict[str, Any]],
    temperature: float,
    token_param: str,
    max_tokens: int | None,
    response_format: dict[str, Any] | None,
    reasoning_effort: str,
    disable_reasoning: bool,
    omit_extra_body_reasoning: bool,
) -> dict[str, Any]:
    """Assemble one chat-completions kwargs dict under a resolved profile.

    Rules (identical for Router/Extractor/Judge, text and image paths):
    - token budget travels via the profile's token parameter, when set;
    - a truthy ``reasoning_effort`` is sent top-level and ALWAYS replaces
      the legacy ``extra_body`` flag (never send both);
    - otherwise the legacy flag is sent only when ``disable_reasoning`` is
      set AND the profile does not omit it;
    - ``response_format`` is included verbatim when provided (tier selection
      lives with the caller); credentials never appear here.
    """
    kwargs: dict[str, Any] = {"model": model, "messages": messages, "temperature": temperature}
    if response_format is not None:
        kwargs["response_format"] = response_format
    if max_tokens is not None:
        kwargs[token_param or "max_tokens"] = max_tokens
    if reasoning_effort:
        kwargs["reasoning_effort"] = reasoning_effort
    elif disable_reasoning and not omit_extra_body_reasoning:
        kwargs["extra_body"] = {"reasoning": {"enabled": False}}
    return kwargs
