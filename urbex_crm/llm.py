"""Provider-agnostic LLM adapter.

- anthropic: official SDK (default). Claude Opus 5.5.
- openai / xai / hermes / openai_compatible: any chat-completions endpoint via httpx,
  so Grok (api.x.ai), ChatGPT (api.openai.com) and Nous Hermes / Ollama / vLLM
  all work by changing LLM_PROVIDER and a base URL.
"""
from __future__ import annotations

import json
import re
from typing import Any

import httpx

from .config import settings

OPENAI_COMPAT_ENDPOINTS = {
    "openai": ("https://api.openai.com/v1", "openai_api_key", "openai_model"),
    "xai": ("https://api.x.ai/v1", "xai_api_key", "xai_model"),
    "hermes": (None, "openai_compatible_api_key", "openai_compatible_model"),
    "openai_compatible": (None, "openai_compatible_api_key", "openai_compatible_model"),
}


class LLMError(RuntimeError):
    pass


def extract_json(text: str) -> Any:
    """Pull the first JSON object/array out of a model response."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = min([i for i in (text.find("{"), text.find("[")) if i >= 0] or [-1])
    if start < 0:
        raise LLMError(f"no JSON in response: {text[:200]}")
    depth, in_str, esc = 0, False, False
    for i, ch in enumerate(text[start:], start):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : i + 1])
    raise LLMError("unterminated JSON in response")


def _anthropic_complete(system: str, user: str, max_tokens: int) -> str:
    try:
        import anthropic
    except ImportError as e:  # pragma: no cover
        raise LLMError("pip install anthropic") from e
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key or None)
    response = client.messages.create(
        model=settings.anthropic_model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    if response.stop_reason == "refusal":
        raise LLMError("model declined the request")
    return "".join(block.text for block in response.content if block.type == "text")


def _openai_compat_complete(system: str, user: str, max_tokens: int) -> str:
    provider = settings.llm_provider
    default_base, key_attr, model_attr = OPENAI_COMPAT_ENDPOINTS[provider]
    base = settings.openai_compatible_base_url or default_base
    if not base:
        raise LLMError(f"OPENAI_COMPATIBLE_BASE_URL is required for provider '{provider}'")
    api_key = getattr(settings, key_attr)
    model = getattr(settings, model_attr)
    if not model:
        raise LLMError(f"no model configured for provider '{provider}'")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "max_tokens": max_tokens,
    }
    r = httpx.post(f"{base.rstrip('/')}/chat/completions", json=payload, headers=headers, timeout=120)
    if r.status_code >= 400:
        raise LLMError(f"{provider} {r.status_code}: {r.text[:300]}")
    return r.json()["choices"][0]["message"]["content"]


def complete(system: str, user: str, max_tokens: int = 2000) -> str:
    provider = settings.llm_provider
    if provider == "anthropic":
        return _anthropic_complete(system, user, max_tokens)
    if provider in OPENAI_COMPAT_ENDPOINTS:
        return _openai_compat_complete(system, user, max_tokens)
    raise LLMError(f"unknown LLM_PROVIDER '{provider}'")


def complete_json(system: str, user: str, max_tokens: int = 2000) -> Any:
    return extract_json(complete(system + "\n\nRespond with JSON only, no prose.", user, max_tokens))


# ---------------------------------------------------------------- prompts --

CLASSIFY_SYSTEM = """You classify Spanish/European B2B companies for a sales CRM.
Sectors: automotive_dealer (concesionarios), automotive_services (talleres, renting, flotas),
construction (constructoras/contratistas), construction_developer (promotoras),
construction_materials (suppliers), other.
Tier = income bracket of the business: high (premium brands or revenue >= 20M EUR / >= 100 staff),
mid (mainstream brands or 3-20M EUR), low (budget/used-car lots, small local builders).
Return {"sector": ..., "sub_sector": short snake_case or null, "tier": "high|mid|low",
"confidence": 0-1, "reason": one sentence}."""


def classify_with_llm(name: str, description: str | None, website: str | None, extra: str | None = None) -> dict:
    user = f"Company: {name}\nWebsite: {website or '-'}\nDescription: {description or '-'}\nExtra: {extra or '-'}"
    return complete_json(CLASSIFY_SYSTEM, user, max_tokens=400)


PERSONALIZE_SYSTEM = """You write one short opening line (max 30 words, same language as the
template) for a cold B2B email, referencing something specific and true about the company
from the facts provided. No flattery, no exclamation marks, no invented facts. If the facts
are too thin, return an empty string. Return {"hook": "..."}."""


def personalize_hook(company_name: str, facts: str, language: str = "es") -> str:
    user = f"Language: {language}\nCompany: {company_name}\nFacts:\n{facts}"
    try:
        return str(complete_json(PERSONALIZE_SYSTEM, user, max_tokens=200).get("hook", "")).strip()
    except LLMError:
        return ""


ROUTER_SYSTEM = """You translate a user's natural-language request (Spanish or English) into
one CRM command. Commands and their args:
- add_company {name, domain?, sector?, notes?}
- list_companies {sector?, tier?, status?, limit?}
- find_contacts {company: name or id}
- draft {company: name or id}
- queue {}
- send_now {}
- stats {}
- pause {}  - resume {}
- stop_contact {email}
- help {}
Return {"command": "...", "args": {...}, "confidence": 0-1}. Use "help" when unsure."""


def route_natural_language(text: str) -> dict:
    return complete_json(ROUTER_SYSTEM, text, max_tokens=300)
