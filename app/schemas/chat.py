"""
Chat schemas for FastPrint ERP LangGraph AI Orchestrator API.
Provides request and response validation models for the /api/v1/chat endpoint.
"""
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator


class ChatRequest(BaseModel):
    """
    Incoming request model for ERP AI Chat Endpoint.
    """
    model_config = ConfigDict(extra="ignore")

    query: str = Field(..., min_length=1, description="Natural language query from user")
    company_id: Optional[int] = Field(default=1, description="Target ERP company ID (defaults to 1)")
    user_token: Optional[str] = Field(default=None, description="Optional JWT authentication token")
    thread_id: Optional[str] = Field(default=None, description="Session thread ID for conversational memory")

    @field_validator("query")
    @classmethod
    def validate_non_empty_query(cls, v: str) -> str:
        """Ensure query is not empty or pure whitespace."""
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("Query string cannot be empty or whitespace only.")
        return cleaned


class ChatResponse(BaseModel):
    """
    Outgoing response model for ERP AI Chat Endpoint.
    """
    model_config = ConfigDict(extra="ignore")

    final_answer: Optional[str] = Field(default=None, description="Synthesized Markdown answer")
    detected_domain: Optional[str] = Field(default=None, description="Classified intent domain")
    verification_status: Optional[str] = Field(default=None, description="Verification status (VALID, EMPTY, RETRY, ERROR)")
    error_message: Optional[str] = Field(default=None, description="Error message if execution encountered failures")
