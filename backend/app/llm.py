"""Thin adapter around the OpenAI SDK, so the rest of the code doesn't depend on it
directly and tests can swap in a fake client."""

from __future__ import annotations

from typing import Any

from app import config


class LLMNotConfigured(RuntimeError):
    pass


def get_client() -> Any:
    if not config.OPENAI_API_KEY:
        raise LLMNotConfigured("OPENAI_API_KEY is not set in backend/.env")
    from openai import OpenAI  # imported lazily: not needed for offline runs

    return OpenAI(api_key=config.OPENAI_API_KEY, max_retries=3, timeout=60)


def parse_structured(client: Any, *, model: str, messages: list, response_format: Any) -> Any:
    """Chat completion whose output is validated against a Pydantic model.

    Works with both newer (client.chat.completions.parse) and older
    (client.beta.chat.completions.parse) SDK versions.
    """
    completions = client.chat.completions
    parse = getattr(completions, "parse", None) or client.beta.chat.completions.parse
    response = parse(model=model, messages=messages, response_format=response_format)
    message = response.choices[0].message
    if getattr(message, "refusal", None):
        raise RuntimeError(f"model refused: {message.refusal}")
    return message.parsed
