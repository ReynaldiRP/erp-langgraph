"""
LLM Model Factory supporting OpenRouter (with free model catalog & auto-fallback) and OpenAI.
"""
import logging
import time
from typing import Any, Dict, List, Optional
import httpx
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI
import free_llm_models as flm

from app.core.config import settings

logger = logging.getLogger("llm_factory")

# In-memory cache for dynamic free models list to avoid redundant API queries
_CACHED_FREE_MODELS: List[str] = []
_LAST_CACHE_TIME: float = 0.0
_CACHE_TTL_SECONDS: float = 900.0  # 15 minutes

# Curated fallback list when offline or when dynamic lookup is unavailable
DEFAULT_CURATED_FREE_MODELS = [
    "openrouter/free",
    "google/gemma-4-31b-it:free",
    "google/gemma-4-26b-a4b-it:free",
    "nvidia/nemotron-3.5-lightning:free",
    "liquid/lfm-2.5-2.6b:free",
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
    "recursive/flint-v1.0:free",
]


def fetch_free_models_catalog(
    api_key: Optional[str] = None,
    force_refresh: bool = False,
    prioritize_tools: bool = True,
) -> List[str]:
    """
    Fetch and return an active list of free models on OpenRouter, cross-referenced
    with the `free_llm_models` library to prioritize models supporting tool calling & JSON output.

    Args:
        api_key: Optional OpenRouter API key for live discovery.
        force_refresh: Bypass in-memory cache if True.
        prioritize_tools: If True, models known to support tools/JSON appear first.

    Returns:
        List of OpenRouter model identifier strings (e.g. ['openrouter/free', ...]).
    """
    global _CACHED_FREE_MODELS, _LAST_CACHE_TIME
    now = time.time()

    if not force_refresh and _CACHED_FREE_MODELS and (now - _LAST_CACHE_TIME < _CACHE_TTL_SECONDS):
        return list(_CACHED_FREE_MODELS)

    discovered_models: List[str] = []
    key = api_key or settings.OPENROUTER_API_KEY or settings.OPENAI_API_KEY

    # 1. Live query to OpenRouter API /v1/models if key is available and not a test mock
    if key and not key.startswith("mock"):
        try:
            headers = {
                "Authorization": f"Bearer {key}",
                "HTTP-Referer": "https://fastprint.co.id",
                "X-Title": "FastPrint ERP LangGraph",
            }
            with httpx.Client(timeout=settings.REQUEST_TIMEOUT_SEC) as client:
                res = client.get("https://openrouter.ai/api/v1/models", headers=headers)
                if res.status_code == 200:
                    data = res.json().get("data", [])
                    for item in data:
                        mid = item.get("id", "")
                        pricing = item.get("pricing", {})
                        prompt_p = float(pricing.get("prompt", 1) or 1)
                        comp_p = float(pricing.get("completion", 1) or 1)
                        if mid.endswith(":free") or (prompt_p == 0 and comp_p == 0):
                            if mid not in discovered_models:
                                discovered_models.append(mid)
        except Exception as exc:
            logger.warning(f"Could not fetch dynamic free models from OpenRouter: {exc}")

    # 2. Merge with curated fallback models if discovery returned empty
    if not discovered_models:
        discovered_models = list(DEFAULT_CURATED_FREE_MODELS)

    # Always ensure 'openrouter/free' meta-router is present
    if "openrouter/free" not in discovered_models:
        discovered_models.insert(0, "openrouter/free")

    # 3. Sort / prioritize using `free_llm_models` metadata
    if prioritize_tools:
        def model_priority(model_id: str) -> int:
            if model_id == "openrouter/free":
                return 0  # Highest priority
            base_name = model_id.split("/")[-1].replace(":free", "")
            matches = flm.search(base_name)
            if matches:
                supports_tools = matches[0].get("supportsTools", False)
                supports_json = matches[0].get("supportsJson", False)
                if supports_tools and supports_json:
                    return 1
                if supports_tools or supports_json:
                    return 2
            return 3

        discovered_models.sort(key=model_priority)

    # 4. Update Cache
    _CACHED_FREE_MODELS = discovered_models
    _LAST_CACHE_TIME = now
    return list(_CACHED_FREE_MODELS)


def get_chat_model(
    provider: Optional[str] = None,
    model_name: Optional[str] = None,
    temperature: float = 0.0,
    fallback_models: Optional[List[str]] = None,
    enable_fallback: bool = True,
) -> BaseChatModel:
    """
    Instantiate and return a ChatModel based on provider.

    Supported providers:
    - 'openrouter': OpenRouter OpenAI-compatible API with automatic free-model fallback.
    - 'openai': Official OpenAI API.

    Args:
        provider: 'openrouter' or 'openai' (defaults to settings.MODEL_PROVIDER).
        model_name: Target model identifier.
        temperature: Sampling temperature (default 0.0 for deterministic output).
        fallback_models: Explicit list of fallback model IDs for OpenRouter.
        enable_fallback: If True, attaches dynamic fallback models array for OpenRouter.

    Returns:
        Configured BaseChatModel instance.
    """
    selected_provider = (provider or settings.MODEL_PROVIDER).lower().strip()

    if selected_provider == "openrouter":
        api_key = settings.OPENROUTER_API_KEY or settings.OPENAI_API_KEY
        if not api_key:
            raise ValueError("OPENROUTER_API_KEY environment variable is required when MODEL_PROVIDER=openrouter")

        target_model = model_name or settings.OPENROUTER_MODEL

        # Configure model fallback array for high-availability
        extra_body: Dict[str, Any] = {}
        if enable_fallback:
            if fallback_models:
                fallbacks = list(fallback_models)
            else:
                active_free = fetch_free_models_catalog(api_key=api_key)
                fallbacks = [m for m in active_free if m != target_model]

            # Construct OpenRouter fallback hierarchy: [primary, *fallbacks]
            # OpenRouter enforces that the 'models' parameter must contain 3 items or fewer.
            model_chain = [target_model] + [m for m in fallbacks if m != target_model]
            extra_body["models"] = model_chain[:3]  # Max 3 models allowed by OpenRouter API

        return ChatOpenAI(
            model=target_model,
            api_key=api_key,
            base_url="https://openrouter.ai/api/v1",
            temperature=temperature,
            request_timeout=settings.REQUEST_TIMEOUT_SEC,
            default_headers={
                "HTTP-Referer": "https://fastprint.co.id",
                "X-Title": "FastPrint ERP LangGraph",
            },
            extra_body=extra_body if extra_body else None,
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
            request_timeout=settings.REQUEST_TIMEOUT_SEC,
        )

    else:
        raise ValueError(
            f"Unsupported MODEL_PROVIDER '{selected_provider}'. Supported values: 'openrouter', 'openai'."
        )
