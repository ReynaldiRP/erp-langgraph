"""
Summarizer Node for FastPrint ERP LangGraph Agent Workflow.
Evaluates conversation history length against threshold and generates a concise,
structured entity summary to maintain long-term context across extended sessions.
"""
import logging
import os
import sys
from typing import Any, Dict, Optional
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import SystemMessage, HumanMessage

from app.core.config import settings
from app.core.context import format_deterministic_summary
from app.graph.state import AgentState
from app.services.llm import get_chat_model

logger = logging.getLogger("summarizer_node")


def summarizer_node(
    state: AgentState,
    llm: Optional[BaseChatModel] = None,
) -> Dict[str, Any]:
    """
    LangGraph pipeline node that evaluates conversational message count.

    Fast Pass-Through:
    If message count <= SUMMARIZATION_THRESHOLD_MESSAGES or auto-summarization is disabled,
    returns an empty dict immediately (0 ms overhead).

    Summarization Cycle:
    When threshold is exceeded, extracts persistent business context (Company ID,
    Vendors, SKUs, Accounts, Dates) to preserve memory without overflowing context window.

    Args:
        state: Current AgentState.
        llm: Optional BaseChatModel for summarization.

    Returns:
        Partial AgentState update dictionary with updated 'conversation_summary'.
    """
    if not settings.ENABLE_AUTO_SUMMARIZATION:
        return {}

    messages = state.get("messages") or []
    # Fast pass-through check (0 ms latency)
    if len(messages) <= settings.SUMMARIZATION_THRESHOLD_MESSAGES:
        return {}

    # Check if LLM is provided or discoverable
    model = llm
    is_test_env = "unittest" in sys.modules or os.environ.get("FASTPRINT_TEST_ENV") == "true"
    if model is None and not is_test_env:
        try:
            model = get_chat_model()
        except Exception:
            model = None

    if model is not None:
        try:
            system_prompt = (
                "You are an ERP Context Summarizer for FastPrint ERP.\n"
                "Summarize key business entities, filters, and active topics from the conversation history.\n"
                "Keep the summary strictly concise and factual:\n"
                "- Active Company ID\n"
                "- Specific Vendor / Supplier Names\n"
                "- Product Names / SKU Codes\n"
                "- Date Ranges / Periods mentioned\n"
                "- Account Codes / Balances under review"
            )

            # Build readable history string for the summarizer prompt
            history_text_blocks = []
            for msg in messages:
                role = "User" if isinstance(msg, HumanMessage) else "Assistant"
                content = getattr(msg, "content", "") or ""
                if content.strip():
                    history_text_blocks.append(f"{role}: {content.strip()}")

            history_payload = "\n".join(history_text_blocks[-15:])
            llm_messages = [
                SystemMessage(content=system_prompt),
                HumanMessage(
                    content=(
                        f"Summarize the key business entities and context from this ERP conversation:\n\n"
                        f"{history_payload}"
                    )
                ),
            ]
            response = model.invoke(llm_messages)
            summary_content = getattr(response, "content", None)
            if summary_content and isinstance(summary_content, str) and summary_content.strip():
                return {"conversation_summary": summary_content.strip()}
        except Exception as exc:
            logger.warning(f"LLM summarizer failed, falling back to deterministic summary: {exc}")

    # Deterministic fallback summary
    det_summary = format_deterministic_summary(messages)
    return {"conversation_summary": det_summary}
