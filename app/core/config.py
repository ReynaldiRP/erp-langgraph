"""
Application Configuration settings loaded from environment variables and .env file.
"""
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 0. Service Configuration
    APP_NAME: str = "erp-ai-orchestrator"
    APP_ENV: str = "development"
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8001
    LOG_LEVEL: str = "info"

    # 1. ERP Backend Connection
    ERP_BASE_URL: str = "http://127.0.0.1:8000"
    DEFAULT_COMPANY_ID: int = 1
    ERP_API_TOKEN: Optional[str] = None
    ERP_USERNAME: Optional[str] = None
    ERP_PASSWORD: Optional[str] = None

    # 2. LLM Provider Selection: 'openrouter' or 'openai'
    MODEL_PROVIDER: str = "openrouter"

    # OpenRouter Configuration
    OPENROUTER_API_KEY: Optional[str] = None
    OPENROUTER_MODEL: str = "meta-llama/llama-3.3-70b-instruct:free"

    # OpenAI Configuration
    OPENAI_API_KEY: Optional[str] = None
    OPENAI_MODEL: str = "gpt-4o-mini"

    # 3. LangGraph & Tools Settings
    USE_DYNAMIC_TOOLS: bool = False
    ENABLE_HITL_INTERRUPT: bool = False


settings = Settings()
