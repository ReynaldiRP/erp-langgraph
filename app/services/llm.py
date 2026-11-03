"""
LLM Model Factory supporting OpenRouter (flexible multi-model) and OpenAI.
"""
from typing import Optional
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI

from app.core.config import settings


def get_chat_model(
    provider: Optional[str] = None,
    model_name: Optional[str] = None,
    temperature: float = 0.0,
) -> BaseChatModel:
    """
    Instantiate and return a ChatModel based on provider.

    Supported providers:
    - 'openrouter': OpenRouter OpenAI-compatible API
    - 'openai': Official OpenAI API
    """
    selected_provider = (provider or settings.MODEL_PROVIDER).lower().strip()

    if selected_provider == "openrouter":
        api_key = settings.OPENROUTER_API_KEY or settings.OPENAI_API_KEY
        if not api_key:
            raise ValueError("OPENROUTER_API_KEY environment variable is required when MODEL_PROVIDER=openrouter")

        target_model = model_name or settings.OPENROUTER_MODEL
        return ChatOpenAI(
            model=target_model,
            api_key=api_key,
            base_url="https://openrouter.ai/api/v1",
            temperature=temperature,
            default_headers={"HTTP-Referer": "https://fastprint.co.id", "X-Title": "FastPrint ERP LangGraph"},
        )

    elif selected_provider == "openai":
        api_key = settings.OPENAI_API_KEY
        if not api_key:
            raise ValueError("OPENAI_API_KEY environment variable is required when MODEL_PROVIDER=openai")

        target_model = model_name or settings.OPENAI_MODEL
        return ChatOpenAI(
            model=target_model,
            api_key=api_key,
            temperature=temperature,
        )

    else:
        raise ValueError(
            f"Unsupported MODEL_PROVIDER '{selected_provider}'. Supported values: 'openrouter', 'openai'."
        )
