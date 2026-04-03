"""
LLM Service — unified interface with Portkey gateway + direct Groq/Gemini fallback.

Strategy:
  1. If Portkey virtual keys are configured → use Portkey gateway (retries + fallback)
  2. Otherwise → use Groq API directly via OpenAI-compatible endpoint
  3. If neither is configured → return graceful error messages
"""

import hashlib
import json
import logging
from typing import Generator

from cachetools import TTLCache
from openai import OpenAI

from app.config import get_settings

logger = logging.getLogger("sentinel.llm")

settings = get_settings()


def _is_placeholder(value: str) -> bool:
    """Check if a config value is a placeholder or empty."""
    placeholders = {"", "your-primary-virtual-key", "your-fallback-virtual-key"}
    return value.strip().lower() in placeholders


class LLMService:
    """Unified LLM Interface — Portkey gateway with direct API fallback."""

    def __init__(self):
        self._cache: TTLCache = TTLCache(maxsize=512, ttl=300)
        self._client = None
        self._mode = "none"

        # Try Portkey first
        if settings.portkey_api_key and not _is_placeholder(settings.portkey_virtual_key):
            try:
                from portkey_ai import Portkey
                self._client = Portkey(api_key=settings.portkey_api_key)
                self._fallback_config = json.dumps(self._build_portkey_config())
                self._mode = "portkey"
                logger.info("LLM: Using Portkey gateway (model=%s)", settings.llm_model)
            except Exception as e:
                logger.warning("LLM: Portkey init failed: %s", e)

        # Fallback to direct Gemini API via OpenAI-compatible endpoint
        if self._mode == "none" and settings.gemini_api_key:
            self._client = OpenAI(
                api_key=settings.gemini_api_key,
                base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            )
            self._model = "gemini-2.5-flash"
            self._mode = "gemini"
            logger.info("LLM: Using direct Gemini API (model=%s)", self._model)

        # Fallback to direct Groq API
        if self._mode == "none" and settings.llm_api_key:
            self._client = OpenAI(
                api_key=settings.llm_api_key,
                base_url="https://api.groq.com/openai/v1",
            )
            self._model = settings.llm_model or "llama-3.3-70b-versatile"
            self._mode = "groq"
            logger.info("LLM: Using direct Groq API (model=%s)", self._model)

        if self._mode == "none":
            logger.warning("LLM: No API keys configured — chat will return fallback messages")

    def _build_portkey_config(self) -> dict:
        targets: list[dict] = [
            {
                "virtual_key": settings.portkey_virtual_key,
                "override_params": {"model": settings.llm_model},
            }
        ]
        if not _is_placeholder(settings.portkey_fallback_virtual_key):
            targets.append(
                {
                    "virtual_key": settings.portkey_fallback_virtual_key,
                    "override_params": {"model": settings.llm_fallback_model},
                }
            )
        return {
            "strategy": {"mode": "fallback"},
            "retry": {"attempts": 2},
            "targets": targets,
        }

    def _call(self, messages: list, stream: bool = False):
        if self._mode == "portkey":
            return (
                self._client
                .with_options(config=self._fallback_config)
                .chat.completions.create(messages=messages, stream=stream)
            )
        elif self._mode in ("groq", "gemini"):
            return self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                stream=stream,
                max_tokens=2048,
            )
        else:
            raise RuntimeError("No LLM configured")

    def generate_insight(self, context: str, system_prompt: str | None = None) -> str:
        """Generate qualitative insight with caching."""
        cache_key = hashlib.sha256(context.encode()).hexdigest()
        if cache_key in self._cache:
            return self._cache[cache_key]

        messages: list[dict] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": context})

        try:
            response = self._call(messages)
            result = response.choices[0].message.content
            self._cache[cache_key] = result
            return result
        except Exception as e:
            logger.error("LLM insight generation failed: %s", e)
            return "Analysis complete (LLM insight temporarily unavailable)."

    def generate_chat_response(self, messages: list) -> str:
        """Generate a chat response."""
        try:
            response = self._call(messages)
            return response.choices[0].message.content
        except Exception as e:
            logger.error("LLM chat generation failed: %s", e)
            return "I'm sorry, I'm having trouble connecting to my language model right now. Please try again in a moment."

    def generate_chat_response_stream(self, messages: list) -> Generator[str, None, None]:
        """Generate a streaming chat response. Yields content chunks."""
        try:
            response = self._call(messages, stream=True)
            for chunk in response:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
        except Exception as e:
            logger.error("LLM streaming failed: %s", e)
            yield "I'm sorry, I'm having trouble connecting right now. Please try again."


llm_service = LLMService()
