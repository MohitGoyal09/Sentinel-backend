import logging
import time
from litellm import completion
from app.config import get_settings

logger = logging.getLogger("sentinel.llm")

settings = get_settings()


class LLMService:
    """Unified LLM Interface via LiteLLM with fallback and retry"""

    FALLBACK_MODELS = ["gemini/gemini-2.0-flash", "gemini/gemini-1.5-flash"]

    def __init__(self):
        self.provider = settings.llm_provider
        self.model = settings.llm_model
        self._cache: dict[str, tuple[str, float]] = {}
        self._cache_ttl = 300  # 5 min cache

    def _get_model_string(self, model: str | None = None) -> str:
        m = model or self.model
        return f"{self.provider}/{m}" if "/" not in m else m

    def _call_llm(self, messages: list, model: str | None = None) -> str:
        response = completion(
            model=self._get_model_string(model),
            messages=messages,
            api_key=settings.llm_api_key if settings.llm_api_key else None,
            timeout=30,
        )
        return response.choices[0].message.content

    def generate_insight(self, context: str, system_prompt: str | None = None) -> str:
        """Generate qualitative insight with retry and model fallback"""
        # Check cache
        cache_key = hash(context[:200])
        if cache_key in self._cache:
            cached, ts = self._cache[cache_key]
            if time.time() - ts < self._cache_ttl:
                return cached

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": context})

        # Try primary model with 1 retry
        for attempt in range(2):
            try:
                result = self._call_llm(messages)
                self._cache[cache_key] = (result, time.time())
                return result
            except Exception as e:
                logger.warning("LLM attempt %d failed (%s): %s", attempt + 1, self._get_model_string(), e)
                if attempt == 0:
                    time.sleep(1)

        # Try fallback models
        for fallback in self.FALLBACK_MODELS:
            try:
                logger.info("Trying fallback model: %s", fallback)
                result = self._call_llm(messages, model=fallback)
                self._cache[cache_key] = (result, time.time())
                return result
            except Exception as e:
                logger.warning("Fallback %s failed: %s", fallback, e)

        logger.error("All LLM models exhausted")
        return "Analysis complete (LLM insight temporarily unavailable)."

    def generate_chat_response(self, messages: list) -> str:
        """Generate a chat response from a full message list (with conversation history)"""
        for attempt in range(2):
            try:
                return self._call_llm(messages)
            except Exception as e:
                logger.warning("Chat LLM attempt %d failed: %s", attempt + 1, e)
                if attempt == 0:
                    time.sleep(1)

        for fallback in self.FALLBACK_MODELS:
            try:
                return self._call_llm(messages, model=fallback)
            except Exception as e:
                logger.warning("Chat fallback %s failed: %s", fallback, e)

        return "I'm sorry, I'm having trouble connecting to my language model right now. Please try again in a moment."


llm_service = LLMService()
