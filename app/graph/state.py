"""
State definitions for LangGraph agent workflows in FastPrint ERP AI Orchestrator.
Provides typed state management, conversational memory reducers, and multi-company scoping.
"""
from typing import Annotated, Any, Dict, List, Literal, Optional, TypedDict
from langchain_core.messages import BaseMessage, HumanMessage
from langgraph.graph.message import add_messages

from app.core.config import settings

# Structured verification status literal
VerificationStatus = Literal["VALID", "EMPTY", "RETRY", "ERROR"]


class AgentState(TypedDict, total=False):
    """
    Centralized LangGraph Agent State.

    Combines conversational message history with structured pipeline attributes
    for domain routing, parameter extraction, tool execution, and guardrail verification.
    """

    # 1. Conversational Memory (LangGraph checkpointer & memory integration)
    messages: Annotated[List[BaseMessage], add_messages]

    # 2. User Context & Multi-Company Scoping
    user_query: str
    user_token: Optional[str]
    company_id: Optional[int]

    # 3. Domain & Tool Routing
    detected_domain: Optional[str]        # e.g., "accounting", "inventory"
    detected_tool: Optional[str]          # e.g., "get_account_balance", "get_journal_entries"
    extracted_params: Optional[Dict[str, Any]]

    # 4. Tool Execution & Output Data
    tool_result: Optional[Dict[str, Any]] # Raw data & calculations returned from ERP

    # 5. Guardrail & Verification Cycle
    verification_status: Optional[VerificationStatus] # "VALID", "EMPTY", "RETRY", "ERROR"
    retry_count: int
    retry_instruction: Optional[str]

    # 6. Final Presentation & Error Reporting
    final_answer: Optional[str]
    error_message: Optional[str]


def create_initial_state(
    user_query: str,
    user_token: Optional[str] = None,
    company_id: Optional[int] = None,
    messages: Optional[List[BaseMessage]] = None,
) -> AgentState:
    """
    Construct a clean initial AgentState dictionary with standard defaults.

    Args:
        user_query: The incoming natural language query string from the user.
        user_token: Optional user JWT access token.
        company_id: Optional target company ID (defaults to settings.DEFAULT_COMPANY_ID).
        messages: Optional pre-existing message list (if None, initializes with HumanMessage).

    Returns:
        A fully initialized AgentState dictionary.
    """
    initial_messages: List[BaseMessage] = (
        list(messages) if messages is not None else [HumanMessage(content=user_query)]
    )

    return {
        "messages": initial_messages,
        "user_query": user_query,
        "user_token": user_token,
        "company_id": company_id if company_id is not None else settings.DEFAULT_COMPANY_ID,
        "detected_domain": None,
        "detected_tool": None,
        "extracted_params": None,
        "tool_result": None,
        "verification_status": None,
        "retry_count": 0,
        "retry_instruction": None,
        "final_answer": None,
        "error_message": None,
    }
