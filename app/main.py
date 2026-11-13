"""
Main FastAPI application entry point for ERP AI Orchestrator.
"""
import asyncio
from contextlib import asynccontextmanager
import hmac
import logging
import re
from typing import Annotated, Any, Dict, Optional

from fastapi import FastAPI, Header, HTTPException, status

from app.core.config import settings
from app.graph.workflow import arun_workflow
from app.schemas.chat import ChatRequest, ChatResponse
from app.services.llm import afetch_free_models_catalog

logger = logging.getLogger("api_main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for background initialization."""
    try:
        loop = asyncio.get_running_loop()
        if loop.is_running():
            loop.create_task(afetch_free_models_catalog())
    except Exception as exc:
        logger.debug("Catalog pre-warm skipped during lifespan initialization: %s", exc)
    yield


app = FastAPI(
    title=settings.APP_NAME,
    version="0.1.0",
    description="FastPrint ERP Dedicated AI Orchestrator Service",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)


@app.get("/health", summary="Health check", tags=["System"])
async def health_check():
    """
    Health check endpoint returning system status.
    """
    return {"status": "healthy"}


def _extract_bearer_token(authorization: Optional[str]) -> Optional[str]:
    """Extract JWT token from an Authorization: Bearer <token> header."""
    if not authorization or not isinstance(authorization, str):
        return None
    match = re.match(r"^\s*Bearer\s+(.+?)\s*$", authorization, re.IGNORECASE)
    if not match:
        return None
    token = match.group(1).strip()
    return token or None


def _resolve_and_validate_auth(
    payload: ChatRequest,
    authorization: Optional[str] = None,
    x_internal_service_key: Optional[str] = None,
) -> Optional[str]:
    """
    Resolve user JWT from body user_token or Authorization Bearer header,
    optionally enforce X-Internal-Service-Key, and reject missing user tokens
    in production or when REQUIRE_USER_TOKEN is enabled.
    """
    if settings.REQUIRE_SERVICE_KEY:
        expected_key = (settings.ERP_API_TOKEN or "").strip()
        provided_key = (x_internal_service_key or "").strip()
        if not expected_key or not hmac.compare_digest(provided_key, expected_key):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Forbidden: invalid or missing X-Internal-Service-Key.",
            )

    body_token = (payload.user_token or "").strip() or None
    header_token = _extract_bearer_token(authorization)
    effective_token = body_token or header_token

    is_strict_mode = (
        settings.APP_ENV.strip().lower() == "production"
        or bool(settings.REQUIRE_USER_TOKEN)
    )
    if is_strict_mode and not effective_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required: valid user_token or Authorization Bearer header must be provided.",
        )

    return effective_token


@app.post(
    "/api/v1/chat",
    response_model=ChatResponse,
    status_code=status.HTTP_200_OK,
    summary="Execute conversational AI workflow for FastPrint ERP",
    tags=["Chat"],
)
async def chat_endpoint(
    payload: ChatRequest,
    authorization: Annotated[Optional[str], Header()] = None,
    x_internal_service_key: Annotated[Optional[str], Header(alias="X-Internal-Service-Key")] = None,
) -> ChatResponse:
    """
    Handle natural language ERP inquiries via LangGraph StateGraph pipeline.
    """
    try:
        effective_token = _resolve_and_validate_auth(
            payload=payload,
            authorization=authorization,
            x_internal_service_key=x_internal_service_key,
        )

        workflow_kwargs = {
            "user_query": payload.query,
            "user_token": effective_token,
            "company_id": payload.company_id,
            "thread_id": payload.thread_id,
        }
        if payload.history is not None:
            workflow_kwargs["history"] = payload.history

        final_state: Dict[str, Any] = await arun_workflow(**workflow_kwargs)

        raw_status = final_state.get("verification_status")
        if raw_status is not None:
            if hasattr(raw_status, "value"):
                verification_status_str = str(raw_status.value)
            else:
                verification_status_str = str(raw_status)
        else:
            verification_status_str = None

        return ChatResponse(
            final_answer=final_state.get("final_answer"),
            detected_domain=final_state.get("detected_domain"),
            verification_status=verification_status_str,
            error_message=final_state.get("error_message"),
            suggested_followups=final_state.get("suggested_followups") or [],
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Unhandled error processing chat endpoint request: %s", exc)
        detail = (
            "Chat workflow failed due to an internal server error."
            if settings.APP_ENV.strip().lower() == "production"
            else f"Chat workflow failed: {str(exc)}"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=detail,
        )


__all__ = ["app", "ChatRequest", "ChatResponse"]


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=settings.APP_HOST, port=settings.APP_PORT, reload=True)

