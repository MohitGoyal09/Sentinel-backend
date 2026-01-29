from litellm import completion
from app.config import get_settings

settings = get_settings()

class LLMService:
    """Unified LLM Interface via LiteLLM"""
    
    def __init__(self):
        self.provider = settings.llm_provider
        self.model = settings.llm_model
        # LiteLLM typically grabs keys from env, but we can pass explicitly if needed
        # Ensure ENV variables like GEMINI_API_KEY / ANTHROPIC_API_KEY are set if not using unified LLM_API_KEY logic
    
    def generate_insight(self, context: str) -> str:
        """Generate qualitative insight based on analysis data"""
        try:
            response = completion(
                model=f"{self.provider}/{self.model}" if "/" not in self.model else self.model,
                messages=[{"role": "user", "content": context}],
                api_key=settings.llm_api_key if settings.llm_api_key else None
            )
            return response.choices[0].message.content
        except Exception as e:
            print(f"LLM Generation Error: {e}")
            return "Analysis complete (LLM insight unavailable)."

llm_service = LLMService()
