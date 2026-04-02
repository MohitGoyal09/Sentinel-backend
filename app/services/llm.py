import json
import logging
import time
from typing import Generator
from portkey_ai import Portkey
from app.config import get_settings

logger = logging.getLogger("sentinel.llm")

settings = get_settings()


class LLMService:
    """Unified LLM Interface via Portkey AI Gateway with native fallback"""

    def __init__(self):
        self._client = Portkey(api_key=settings.portkey_api_key)
        self._fallback_config = json.dumps(self._build_fallback_config())
        self._cache: dict[str, tuple[str, float]] = {}
        self._cache_ttl = 300  # 5 min cache

    def _build_fallback_config(self) -> dict:
        targets: list[dict] = [
            {
                "virtual_key": settings.portkey_virtual_key,
                "override_params": {"model": settings.llm_model},
            }
        ]
        if settings.portkey_fallback_virtual_key:
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
        return (
            self._client
            .with_options(config=self._fallback_config)
            .chat.completions.create(messages=messages, stream=stream)
        )

    def generate_insight(self, context: str, system_prompt: str | None = None) -> str:
        """Generate qualitative insight with caching. Portkey handles retries/fallback."""
        cache_key = hash(context[:200])
        if cache_key in self._cache:
            cached, ts = self._cache[cache_key]
            if time.time() - ts < self._cache_ttl:
                return cached

        messages: list[dict] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": context})

        try:
            response = self._call(messages)
            result = response.choices[0].message.content
            self._cache[cache_key] = (result, time.time())
            return result
        except Exception as e:
            logger.error("LLM insight generation failed: %s", e)
            return "Analysis complete (LLM insight temporarily unavailable)."

    def generate_chat_response(self, messages: list) -> str:
        """Generate a chat response. Portkey handles retries/fallback."""
        try:
            response = self._call(messages)
            return response.choices[0].message.content
        except Exception as e:
            logger.error("LLM chat generation failed: %s", e)
            return "I'm sorry, I'm having trouble connecting to my language model right now. Please try again in a moment."

    def generate_chat_response_stream(self, messages: list) -> Generator[str, None, None]:
        """Generate a streaming chat response. Yields content chunks as strings."""
        try:
            response = self._call(messages, stream=True)
            for chunk in response:
                content = chunk.choices[0].delta.content
                if content:
                    yield content
        except Exception as e:
            logger.error("LLM streaming failed: %s", e)
            yield "I'm sorry, I'm having trouble connecting right now. Please try again."


llm_service = LLMService()
