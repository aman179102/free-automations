from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx

from common.config import Settings

logger = logging.getLogger(__name__)


def _extract_json(value: str) -> dict[str, Any] | None:
    """Robustly extracts and parses a JSON object from raw LLM output."""
    if not value:
        return None
    text = value.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].lstrip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _post_with_retry(
    client: httpx.Client,
    url: str,
    *,
    headers: dict[str, str],
    payload: dict[str, Any],
    provider: str,
) -> httpx.Response | None:
    """Sends HTTP POST request with exponential backoff on retriable errors."""
    for attempt in range(3):
        try:
            response = client.post(url, headers=headers, json=payload)
        except httpx.HTTPError as exc:
            logger.warning("%s request network error: %s", provider, exc.__class__.__name__)
            if attempt < 2:
                time.sleep(1.5 ** attempt)
                continue
            return None

        if response.status_code == 200:
            return response

        # Retriable server / rate-limit errors
        if response.status_code in {429, 500, 502, 503, 504} and attempt < 2:
            sleep_sec = 2 ** attempt
            logger.warning(
                "%s returned HTTP %s (rate-limited or busy). Retrying in %ss...",
                provider,
                response.status_code,
                sleep_sec,
            )
            time.sleep(sleep_sec)
            continue

        logger.warning(
            "%s returned HTTP %s: %s", provider, response.status_code, response.text[:250]
        )
        return None

    return None


def _gemini_generate(
    client: httpx.Client,
    settings: Settings,
    system: str,
    user: str,
    *,
    json_mode: bool,
    temperature: float,
) -> str | None:
    """Queries Gemini Generative Language API across configured models."""
    if not settings.gemini_api_key:
        return None

    prompt_combined = f"{system}\n\n{user}" if system else user

    for model in settings.gemini_models:
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent?key={settings.gemini_api_key}"
        )
        gen_config: dict[str, Any] = {"temperature": temperature}
        if json_mode:
            gen_config["responseMimeType"] = "application/json"

        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt_combined}]}],
            "generationConfig": gen_config,
        }
        res = _post_with_retry(
            client,
            url,
            headers={"Content-Type": "application/json"},
            payload=payload,
            provider=f"Gemini ({model})",
        )
        if not res:
            continue
        try:
            candidates = res.json().get("candidates", [])
            text = candidates[0]["content"]["parts"][0]["text"]
            if text and text.strip():
                return text.strip()
        except (KeyError, IndexError, TypeError):
            continue

    return None


def _groq_generate(
    client: httpx.Client,
    settings: Settings,
    system: str,
    user: str,
    *,
    json_mode: bool,
    temperature: float,
) -> str | None:
    """Queries Groq Chat Completions API across configured models."""
    if not settings.groq_api_key:
        return None

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.groq_api_key}",
        "Content-Type": "application/json",
    }
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user})

    for model in settings.groq_models:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        res = _post_with_retry(
            client,
            url,
            headers=headers,
            payload=payload,
            provider=f"Groq ({model})",
        )
        if not res:
            continue
        try:
            content = res.json()["choices"][0]["message"]["content"]
            if content and content.strip():
                return content.strip()
        except (KeyError, IndexError, TypeError):
            continue

    return None


def _openrouter_generate(
    client: httpx.Client,
    settings: Settings,
    system: str,
    user: str,
    *,
    json_mode: bool,
    temperature: float,
) -> str | None:
    """Queries OpenRouter API across configured models."""
    if not settings.openrouter_api_key:
        return None

    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
    }
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user})

    for model in settings.openrouter_models:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        res = _post_with_retry(
            client,
            url,
            headers=headers,
            payload=payload,
            provider=f"OpenRouter ({model})",
        )
        if not res:
            continue
        try:
            content = res.json()["choices"][0]["message"]["content"]
            if content and content.strip():
                return content.strip()
        except (KeyError, IndexError, TypeError):
            continue

    return None


def generate_json(
    settings: Settings,
    system: str,
    user: str,
    temperature: float = 0.3,
) -> dict[str, Any] | None:
    """
    Attempts JSON generation via Gemini, falling back to Groq, then OpenRouter.
    Returns parsed dictionary or None if all providers fail.
    """
    with httpx.Client(timeout=20.0) as client:
        # 1. Primary: Gemini
        raw_gemini = _gemini_generate(client, settings, system, user, json_mode=True, temperature=temperature)
        if raw_gemini:
            parsed = _extract_json(raw_gemini)
            if parsed:
                return parsed

        # 2. Secondary Fallback: Groq
        raw_groq = _groq_generate(client, settings, system, user, json_mode=True, temperature=temperature)
        if raw_groq:
            parsed = _extract_json(raw_groq)
            if parsed:
                return parsed

        # 3. Tertiary Fallback: OpenRouter
        raw_openrouter = _openrouter_generate(client, settings, system, user, json_mode=True, temperature=temperature)
        if raw_openrouter:
            parsed = _extract_json(raw_openrouter)
            if parsed:
                return parsed

    logger.warning("All AI providers (Gemini, Groq, OpenRouter) exhausted for generate_json.")
    return None


def generate_text(
    settings: Settings,
    system: str,
    user: str,
    temperature: float = 0.7,
) -> str | None:
    """
    Attempts free-form text generation via Gemini, falling back to Groq, then OpenRouter.
    Returns generated string or None if all providers fail.
    """
    with httpx.Client(timeout=20.0) as client:
        # 1. Primary: Gemini
        res = _gemini_generate(client, settings, system, user, json_mode=False, temperature=temperature)
        if res:
            return res

        # 2. Secondary Fallback: Groq
        res = _groq_generate(client, settings, system, user, json_mode=False, temperature=temperature)
        if res:
            return res

        # 3. Tertiary Fallback: OpenRouter
        res = _openrouter_generate(client, settings, system, user, json_mode=False, temperature=temperature)
        if res:
            return res

    logger.warning("All AI providers (Gemini, Groq, OpenRouter) exhausted for generate_text.")
    return None


def test_ai_connectivity(settings: Settings) -> dict[str, bool]:
    """Tests connectivity to all configured AI providers with a trivial prompt."""
    results = {"gemini": False, "groq": False, "openrouter": False}
    with httpx.Client(timeout=10.0) as client:
        if settings.gemini_api_key:
            res = _gemini_generate(client, settings, "Respond with OK", "ping", json_mode=False, temperature=0.0)
            results["gemini"] = bool(res)

        if settings.groq_api_key:
            res = _groq_generate(client, settings, "Respond with OK", "ping", json_mode=False, temperature=0.0)
            results["groq"] = bool(res)

        if settings.openrouter_api_key:
            res = _openrouter_generate(client, settings, "Respond with OK", "ping", json_mode=False, temperature=0.0)
            results["openrouter"] = bool(res)

    return results
