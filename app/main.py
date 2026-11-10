"""
Main FastAPI application entry point for ERP AI Orchestrator.
"""
import asyncio
from contextlib import asynccontextmanager
import logging
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException, status

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


@app.post(
    "/api/v1/chat",
    response_model=ChatResponse,
    status_code=status.HTTP_200_OK,
    summary="Execute conversational AI workflow for FastPrint ERP",
    tags=["Chat"],
)
async def chat_endpoint(payload: ChatRequest) -> ChatResponse:
    """
    Handle natural language ERP inquiries via LangGraph StateGraph pipeline.
    """
    try:
        final_state: Dict[str, Any] = await arun_workflow(
            user_query=payload.query,
            user_token=payload.user_token,
            company_id=payload.company_id,
            thread_id=payload.thread_id,
        )

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
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Unhandled error processing chat endpoint request: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Chat workflow failed: {str(exc)}",
        )


__all__ = ["app", "ChatRequest", "ChatResponse"]


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=settings.APP_HOST, port=settings.APP_PORT, reload=True)

