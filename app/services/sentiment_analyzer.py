"""
Sentiment Analyzer — classifies message sentiment via Gemini.

Privacy guarantee: message text is NEVER stored in the database.
Text is passed to Gemini for classification, the score is returned,
and the text goes out of scope. Only the score persists.
"""

import json
import logging
from typing import Optional

from openai import AsyncOpenAI

from app.config import get_settings

logger = logging.getLogger("sentinel.sentiment")


class SentimentAnalyzer:
    """Classify workplace message sentiment using Gemini 2.5 Flash."""

    def __init__(self) -> None:
        settings = get_settings()
        self._client: Optional[AsyncOpenAI] = None

        if settings.gemini_api_key:
            self._client = AsyncOpenAI(
                api_key=settings.gemini_api_key,
                base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            )
        elif settings.llm_api_key:
            self._client = AsyncOpenAI(
                api_key=settings.llm_api_key,
                base_url="https://api.groq.com/openai/v1",
            )

    async def classify(self, text: str) -> dict:
        """
        Classify sentiment of a workplace message.

        Returns: {"score": "positive"|"neutral"|"negative", "confidence": 0.0-1.0}

        The text parameter is used ONLY for this API call.
        It is never stored, logged, or persisted in any form.
        """
        if not self._client or not text or len(text) < 4:
            return {"score": "neutral", "confidence": 0.0}

        # Cap text length to control cost and reduce prompt injection surface
        text = text[:2000]

        try:
            response = await self._client.chat.completions.create(
                model="gemini-2.5-flash",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You classify workplace message sentiment. "
                            "Reply with ONLY valid JSON, no markdown:\n"
                            '{"score": "positive" or "neutral" or "negative", '
                            '"confidence": 0.0 to 1.0}'
                        ),
                    },
                    {"role": "user", "content": text},
                ],
                temperature=0.1,
                max_tokens=50,
            )

            raw = response.choices[0].message.content.strip()
            # Strip markdown code fences if present
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

            result = json.loads(raw)
            score = result.get("score", "neutral")
            if score not in ("positive", "neutral", "negative"):
                score = "neutral"
            confidence = min(max(float(result.get("confidence", 0.5)), 0.0), 1.0)

            return {"score": score, "confidence": round(confidence, 2)}

        except Exception as e:
            logger.warning("Sentiment classification failed: %s", e)
            return {"score": "neutral", "confidence": 0.0}
