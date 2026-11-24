"""OpenRouter LLM factory for Pipecat eval judge and simulator."""

from pathlib import Path

from dotenv import load_dotenv
from pipecat.services.openrouter.llm import OpenRouterLLMService

from config import load


def openrouter(config: dict) -> OpenRouterLLMService:
    root = Path(__file__).resolve().parent.parent
    load_dotenv(root / ".env")
    settings = load()
    if settings.openrouter_api_key == "":
        raise RuntimeError("OPENROUTER_API_KEY is required for eval LLM services")
    return OpenRouterLLMService(
        api_key=settings.openrouter_api_key,
        settings=OpenRouterLLMService.Settings(model=settings.openrouter_model),
    )