"""
RAG Node for FastPrint ERP LangGraph Agent Workflow.
Executes semantic retrieval for qualitative data (SOPs, product specifications, policies)
via DjangoCoreClient connecting to PGVector on the Django backend.
"""
import asyncio
import concurrent.futures
import logging
from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.graph.state import AgentState
from app.services.django_client import (
    DjangoCoreClient,
    DjangoCoreClientError,
)
from app.services.vector_service import vector_service

logger = logging.getLogger("rag_node")


async def rag_node(
    state: AgentState,
    client: Optional[DjangoCoreClient] = None,
) -> Dict[str, Any]:
    """
    Asynchronous LangGraph node that performs semantic knowledge search.

    Architecture Support:
    1. First checks if Dedicated Vector Service (PGVector + Gemini) is configured.
       If VECTOR_DB_URL and GEMINI_API_KEY are active, queries dedicated PGVector directly.
    2. Fallback to Django Core API endpoint POST /ai/tools/knowledge/search/ if client provided or configured.
    """
    if not settings.ENABLE_RAG:
        return {"rag_context": [], "rag_sources": []}

    rag_query = state.get("rag_query") or state.get("user_query") or ""
    query_mode = state.get("query_mode") or "vector_rag"
    company_id = state.get("company_id") or settings.DEFAULT_COMPANY_ID
    user_token = state.get("user_token")

    if not rag_query.strip():
        return {"rag_context": [], "rag_sources": []}

    chunks: List[Dict[str, Any]] = []

    # Path 1: Check Dedicated Vector Service (PGVector + Gemini) first
    if settings.VECTOR_DB_URL and settings.GEMINI_API_KEY:
        try:
            chunks = await vector_service.search_knowledge(
                query=rag_query.strip(),
                company_id=company_id,
                top_k=settings.RAG_TOP_K,
                threshold=settings.RAG_SIMILARITY_THRESHOLD,
            )
        except Exception as vec_err:
            logger.warning(f"Dedicated vector service search failed: {vec_err}")
            chunks = []

    # Path 2: Fallback / standard path via Django Core API client
    if not chunks and (client is not None or not settings.VECTOR_DB_URL):
        own_client = client is None
        erp_client = client or DjangoCoreClient(require_user_token=settings.REQUIRE_USER_TOKEN)
        try:
            raw_res = await erp_client.fetch_knowledge(
                params={
                    "query": rag_query.strip(),
                    "top_k": settings.RAG_TOP_K,
                },
                user_token=user_token,
                company_id=company_id,
            )
            if isinstance(raw_res, dict):
                chunks = raw_res.get("chunks", []) or raw_res.get("data", {}).get("chunks", [])
            elif isinstance(raw_res, list):
                chunks = raw_res
        except DjangoCoreClientError as err:
            logger.warning(f"DjangoCoreClientError in rag_node: {err}")
            err_delta: Dict[str, Any] = {
                "rag_context": [],
                "rag_sources": [],
            }
            if query_mode == "vector_rag":
                err_delta["verification_status"] = "ERROR"
                err_delta["error_message"] = str(err)
            return err_delta
        except Exception as exc:
            logger.error(f"Unexpected error in rag_node: {exc}", exc_info=True)
            return {"rag_context": [], "rag_sources": []}
        finally:
            if own_client:
                await erp_client.aclose()

    # Filter chunks by similarity threshold
    threshold = float(settings.RAG_SIMILARITY_THRESHOLD)
    valid_chunks: List[Dict[str, Any]] = []
    sources: List[str] = []

    for c in chunks:
        if not isinstance(c, dict):
            continue
        score = float(c.get("similarity_score", 1.0))
        if score >= threshold:
            valid_chunks.append(c)
            title = c.get("title") or c.get("metadata", {}).get("source") or "Dokumen SOP ERP"
            if title not in sources:
                sources.append(title)

    result_delta: Dict[str, Any] = {
        "rag_context": valid_chunks,
        "rag_sources": sources,
    }

    if query_mode == "vector_rag":
        result_delta["verification_status"] = "VALID" if valid_chunks else "EMPTY"

    return result_delta


def rag_node_sync(
    state: AgentState,
    client: Optional[DjangoCoreClient] = None,
) -> Dict[str, Any]:
    """
    Synchronous wrapper for rag_node.
    Enables safe execution in synchronous CLI runners, testing suites, or scripts.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        with concurrent.futures.ThreadPoolExecutor() as executor:
            return executor.submit(asyncio.run, rag_node(state, client=client)).result()
    else:
        return asyncio.run(rag_node(state, client=client))
