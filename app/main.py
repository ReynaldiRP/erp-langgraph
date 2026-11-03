"""
Main FastAPI application entry point for ERP AI Orchestrator.
"""
from fastapi import FastAPI
from app.core.config import settings

app = FastAPI(
    title=settings.APP_NAME,
    version="0.1.0",
    description="FastPrint ERP Dedicated AI Orchestrator Service",
    docs_url="/docs",
    redoc_url="/redoc",
)


@app.get("/health")
async def health_check():
    """
    Health check endpoint returning system status.
    """
    return {"status": "healthy"}
