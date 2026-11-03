"""
LLM Model Factory supporting OpenRouter (free tier / flexible), Google Gemini, and OpenAI.
"""
import os
from typing import Optional
from langchain_core.language_models.chat_models import BaseChatModel


def get_chat_model(
    provider: Optional[str] = None,
    model_name: Optional[str] = None,
    temperature: float = 0.0,
) -> BaseChatModel:
    """
    Instantiate and return a ChatModel based on provider.

    Supported providers:
    - 'openrouter': OpenRouter OpenAI-compatible API (free models or paid)
    - 'gemini': Google Gemini via langchain-google-genai
    - 'openai': OpenAI official API
    - 'ollama': Local Ollama via OpenAI-compatible endpoint
    """
    selected_provider = (provider or os.getenv("MODEL_PROVIDER", "openrouter")).lower().strip()

    if selected_provider == "openrouter":
        from langchain_openai import ChatOpenAI

        api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENROUTER_API_KEY environment variable is required when MODEL_PROVIDER=openrouter")

        target_model = model_name or os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct:free")
        return ChatOpenAI(
            model=target_model,
            api_key=api_key,
            base_url="https://openrouter.ai/api/v1",
            temperature=temperature,
            default_headers={"HTTP-Referer": "https://fastprint.co.id", "X-Title": "FastPrint ERP LangGraph"},
        )

    elif selected_provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY or GOOGLE_API_KEY is required when MODEL_PROVIDER=gemini")

        target_model = model_name or os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        return ChatGoogleGenerativeAI(
            model=target_model,
            google_api_key=api_key,
            temperature=temperature,
        )

    elif selected_provider == "openai":
        from langchain_openai import ChatOpenAI

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY is required when MODEL_PROVIDER=openai")

        target_model = model_name or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        return ChatOpenAI(
            model=target_model,
            api_key=api_key,
            temperature=temperature,
        )

    elif selected_provider == "ollama":
        from langchain_openai import ChatOpenAI

        base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
        target_model = model_name or os.getenv("OLLAMA_MODEL", "llama3.2")
        return ChatOpenAI(
            model=target_model,
            api_key="ollama",
            base_url=base_url,
            temperature=temperature,
        )

    else:
        raise ValueError(
            f"Unsupported MODEL_PROVIDER '{selected_provider}'. Allowed: 'openrouter', 'gemini', 'openai', 'ollama'."
        )
