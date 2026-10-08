"""Tiny provider-agnostic LLM client (plain HTTPS via `requests`, no vendor SDKs).

Set ONE of GROQ_API_KEY / GEMINI_API_KEY / ANTHROPIC_API_KEY / OPENAI_API_KEY.
To add a provider, add a `_call_xxx` function and register it in PROVIDERS.
"""
from __future__ import annotations

import requests

import config

DEFAULT_MODELS = {
    "groq": "openai/gpt-oss-20b",
    "gemini": "gemini-2.5-flash",
    "anthropic": "claude-haiku-4-5-20251001",
    "openai": "gpt-4o-mini",
}
KEY_NAMES = {
    "groq": "GROQ_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
}


def provider() -> str:
    """Returns 'groq' | 'gemini' | 'anthropic' | 'openai' | 'none'."""
    forced = config.get_secret("LLM_PROVIDER").lower().strip()
    if forced in KEY_NAMES and config.get_secret(KEY_NAMES[forced]):
        return forced
    for name, key in KEY_NAMES.items():
        if config.get_secret(key):
            return name
    return "none"


def model_name(prov: str) -> str:
    return config.get_secret("LLM_MODEL") or DEFAULT_MODELS[prov]


def _call_gemini(system: str, user: str, key: str, model: str, timeout: int) -> str:
    r = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        headers={"x-goog-api-key": key, "Content-Type": "application/json"},
        json={
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0},
        },
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json()["candidates"][0]["content"]["parts"][0]["text"]


def _call_anthropic(system: str, user: str, key: str, model: str, timeout: int) -> str:
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={"model": model, "max_tokens": 1000, "system": system, "messages": [{"role": "user", "content": user}]},
        timeout=timeout,
    )
    r.raise_for_status()
    return "".join(b.get("text", "") for b in r.json()["content"])


def _call_openai(system: str, user: str, key: str, model: str, timeout: int) -> str:
    r = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        },
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def _call_groq(system: str, user: str, key: str, model: str, timeout: int) -> str:
    """Groq uses an OpenAI-compatible API — same shape, different base URL.
    NOTE: Groq requires the word 'json' in the messages when using json_object mode.
    """
    # Groq requirement: system prompt must contain 'json' when response_format is json_object
    groq_system = system if "json" in system.lower() else system + "\nRespond with valid JSON only."
    r = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": groq_system}, {"role": "user", "content": user}],
        },
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


PROVIDERS = {"groq": _call_groq, "gemini": _call_gemini, "anthropic": _call_anthropic, "openai": _call_openai}


def call_llm(system: str, user: str, timeout: int = 45) -> str:
    prov = provider()
    if prov == "none":
        raise RuntimeError("No LLM API key configured")
    return PROVIDERS[prov](system, user, config.get_secret(KEY_NAMES[prov]), model_name(prov), timeout)
