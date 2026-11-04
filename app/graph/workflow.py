"""
StateGraph Workflow Assembly & Compilation for FastPrint ERP LangGraph Agent (TASK-4.6).
Orchestrates router, extractor, tool execution, verifier, and synthesizer nodes
into an end-to-end multi-node pipeline with conditional branching, retry cycles,
and multi-turn conversational memory.
"""
import asyncio
import concurrent.futures
import logging
from typing import Any, Dict, List, Optional
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import StateGraph, START, END

from app.core.config import settings
from app.graph.state import AgentState, create_initial_state
from app.graph.nodes.router import router_node
from app.graph.nodes.extractor import extractor_node
from app.graph.nodes.tool_node import tool_node
from app.graph.nodes.verifier import verifier_node, decide_after_verification
from app.graph.nodes.synthesizer import synthesizer_node

logger = logging.getLogger("workflow")


def decide_after_router(state: AgentState) -> str:
    """
    Conditional edge routing after the router node.

    Routing rules:
    - If a specific ERP tool is detected or domain is 'accounting' / 'inventory':
      Routes to 'extractor' to parse query entities and dates.
    - If domain is 'general' or no tool was matched:
      Routes directly to 'synthesizer' to provide general guidance or explain guardrails.

    Args:
        state: Current AgentState.

    Returns:
        Target node identifier ('extractor' or 'synthesizer').
    """
    detected_tool = state.get("detected_tool")
    detected_domain = state.get("detected_domain")

    if detected_tool or detected_domain in ("accounting", "inventory"):
        return "extractor"

    return "synthesizer"


def create_workflow_graph(
    checkpointer: Optional[Any] = None,
    interrupt_before: Optional[List[str]] = None,
):
    """
    Construct, connect, and compile the full StateGraph pipeline.

    Flow:
        START -> router -> (conditional: extractor / synthesizer)
        extractor -> tool_node -> verifier -> (conditional: extractor / synthesizer)
        synthesizer -> END

    Args:
        checkpointer: LangGraph checkpoint saver (defaults to MemorySaver()).
        interrupt_before: List of node names to pause for human approval.

    Returns:
        Compiled LangGraph StateGraph instance.
    """
    workflow = StateGraph(AgentState)

    # 1. Register Pipeline Nodes
    workflow.add_node("router", router_node)
    workflow.add_node("extractor", extractor_node)
    workflow.add_node("tool_node", tool_node)
    workflow.add_node("verifier", verifier_node)
    workflow.add_node("synthesizer", synthesizer_node)

    # 2. Connect Edges
    workflow.add_edge(START, "router")
    workflow.add_conditional_edges(
        "router",
        decide_after_router,
        {
            "extractor": "extractor",
            "synthesizer": "synthesizer",
        },
    )
    workflow.add_edge("extractor", "tool_node")
    workflow.add_edge("tool_node", "verifier")
    workflow.add_conditional_edges(
        "verifier",
        decide_after_verification,
        {
            "extractor": "extractor",
            "synthesizer": "synthesizer",
        },
    )
    workflow.add_edge("synthesizer", END)

    # 3. Checkpointer & HITL Interrupts
    memory = checkpointer if checkpointer is not None else MemorySaver()
    interrupts = (
        interrupt_before
        if interrupt_before is not None
        else (["tool_node"] if settings.ENABLE_HITL_INTERRUPT else [])
    )

    compiled_graph = workflow.compile(
        checkpointer=memory,
        interrupt_before=interrupts,
    )
    return compiled_graph


# Singleton default compiled graph
default_workflow_graph = create_workflow_graph()


async def arun_workflow(
    user_query: str,
    user_token: Optional[str] = None,
    company_id: Optional[int] = None,
    thread_id: Optional[str] = None,
    graph: Optional[Any] = None,
) -> AgentState:
    """
    Asynchronously run the compiled workflow graph for a user query.

    Args:
        user_query: Incoming natural language query.
        user_token: Optional JWT access token.
        company_id: Optional target company ID.
        thread_id: Optional conversational session thread ID for MemorySaver.
        graph: Optional compiled StateGraph instance.

    Returns:
        Final resulting AgentState dictionary.
    """
    active_graph = graph or default_workflow_graph
    initial_state = create_initial_state(
        user_query=user_query,
        user_token=user_token,
        company_id=company_id,
    )

    config = {"configurable": {"thread_id": thread_id or "default_session"}}
    final_state = await active_graph.ainvoke(initial_state, config=config)
    return final_state


def run_workflow_sync(
    user_query: str,
    user_token: Optional[str] = None,
    company_id: Optional[int] = None,
    thread_id: Optional[str] = None,
    graph: Optional[Any] = None,
) -> AgentState:
    """
    Synchronously run the compiled workflow graph (safe for CLI and test runners).
    Handles environments with or without an active asyncio event loop.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    coro = arun_workflow(
        user_query=user_query,
        user_token=user_token,
        company_id=company_id,
        thread_id=thread_id,
        graph=graph,
    )

    if loop and loop.is_running():
        with concurrent.futures.ThreadPoolExecutor() as executor:
            return executor.submit(asyncio.run, coro).result()
    else:
        return asyncio.run(coro)
