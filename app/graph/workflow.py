"""
StateGraph Workflow Assembly & Compilation for FastPrint ERP LangGraph Agent (TASK-4.6).
Orchestrates router, extractor, tool execution, verifier, and synthesizer nodes
into an end-to-end multi-node pipeline with conditional branching, retry cycles,
and multi-turn conversational memory.
"""
import atexit
import asyncio
import concurrent.futures
import logging
import threading
from typing import Any, Dict, List, Optional
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import StateGraph, START, END

from app.core.config import settings
from app.graph.state import AgentState, create_initial_state
from app.graph.nodes.summarizer import summarizer_node
from app.graph.nodes.router import router_node
from app.graph.nodes.rag_node import rag_node
from app.graph.nodes.extractor import extractor_node
from app.graph.nodes.tool_node import tool_node
from app.graph.nodes.verifier import verifier_node, decide_after_verification
from app.graph.nodes.synthesizer import synthesizer_node

logger = logging.getLogger("workflow")


def decide_after_router(state: AgentState) -> str:
    """
    Conditional edge routing after the router node.

    Routing rules:
    - If query_mode is 'vector_rag' or domain is 'knowledge':
      Routes directly to 'rag_node' for semantic document retrieval.
    - If domain is 'general' or 'out_of_scope':
      Routes directly to 'synthesizer' to provide general guidance or explain guardrails.
    - If active_tools contains planned tool calls or legacy detected_tool is set
      for accounting/inventory:
      Routes to 'extractor' to parse query entities and dates (supports multi-tool & single-tool).
    - Otherwise (no tools matched or unrecognized domain):
      Routes directly to 'synthesizer'.

    Args:
        state: Current AgentState.

    Returns:
        Target node identifier ('rag_node', 'extractor', or 'synthesizer').
    """
    query_mode = state.get("query_mode")
    detected_tool = state.get("detected_tool")
    detected_domain = state.get("detected_domain")
    active_tools = state.get("active_tools") or []

    # 1. Vector RAG route for SOPs, specs, and qualitative knowledge
    if query_mode == "vector_rag" or detected_domain == "knowledge":
        return "rag_node"

    # 2. Guardrail: general & out_of_scope inquiries NEVER invoke tool extraction
    if detected_domain in ("general", "out_of_scope"):
        return "synthesizer"

    # 3. Active ERP tools check (supporting multi-tool active_tools & scalar detected_tool)
    has_active_tools = any(
        isinstance(tool_call, dict) and bool(tool_call.get("tool"))
        for tool_call in active_tools
    )
    has_scalar_tool = bool(detected_tool) and detected_domain in ("accounting", "inventory")

    if has_active_tools or has_scalar_tool:
        return "extractor"

    return "synthesizer"


def decide_after_tool(state: AgentState) -> str:
    """
    Routing after live tool execution:
    - Hybrid queries proceed to 'rag_node' to fetch complementary SOP / policy context.
    - Pure live_erp queries proceed directly to 'verifier'.
    """
    if state.get("query_mode") == "hybrid":
        return "rag_node"
    return "verifier"


def decide_after_rag(state: AgentState) -> str:
    """
    Routing after RAG retrieval:
    - Hybrid queries proceed to 'verifier' to validate the tool execution data.
    - Pure vector_rag queries proceed directly to 'synthesizer'.
    """
    if state.get("query_mode") == "hybrid":
        return "verifier"
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
    workflow.add_node("summarizer", summarizer_node)
    workflow.add_node("router", router_node)
    workflow.add_node("rag_node", rag_node)
    workflow.add_node("extractor", extractor_node)
    workflow.add_node("tool_node", tool_node)
    workflow.add_node("verifier", verifier_node)
    workflow.add_node("synthesizer", synthesizer_node)

    # 2. Connect Edges
    workflow.add_edge(START, "summarizer")
    workflow.add_edge("summarizer", "router")
    workflow.add_conditional_edges(
        "router",
        decide_after_router,
        {
            "rag_node": "rag_node",
            "extractor": "extractor",
            "synthesizer": "synthesizer",
        },
    )
    workflow.add_edge("extractor", "tool_node")
    workflow.add_conditional_edges(
        "tool_node",
        decide_after_tool,
        {
            "rag_node": "rag_node",
            "verifier": "verifier",
        },
    )
    workflow.add_conditional_edges(
        "rag_node",
        decide_after_rag,
        {
            "verifier": "verifier",
            "synthesizer": "synthesizer",
        },
    )
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


async def astream_workflow_steps(
    user_query: str,
    user_token: Optional[str] = None,
    company_id: Optional[int] = None,
    thread_id: Optional[str] = None,
    graph: Optional[Any] = None,
):
    """
    Asynchronously stream individual node completion events from the workflow StateGraph.
    Yields tuples of (node_name, node_update_dict, accumulated_state_dict).
    """
    active_graph = graph or default_workflow_graph
    initial_state = create_initial_state(
        user_query=user_query,
        user_token=user_token,
        company_id=company_id,
    )
    config = {"configurable": {"thread_id": thread_id or "default_session"}}
    accumulated_state = dict(initial_state)

    async for chunk in active_graph.astream(initial_state, config=config, stream_mode="updates"):
        for node_name, update in chunk.items():
            if isinstance(update, dict):
                accumulated_state.update(update)
            yield (node_name, update, accumulated_state)


_WORKFLOW_EXECUTOR: Optional[concurrent.futures.ThreadPoolExecutor] = None
_EXECUTOR_LOCK = threading.Lock()


def get_workflow_executor(max_workers: Optional[int] = None) -> concurrent.futures.ThreadPoolExecutor:
    """
    Acquire or instantiate reusable ThreadPoolExecutor for run_workflow_sync.
    Thread-safe and lazily initialized.
    """
    global _WORKFLOW_EXECUTOR
    if _WORKFLOW_EXECUTOR is None or getattr(_WORKFLOW_EXECUTOR, "_shutdown", False):
        with _EXECUTOR_LOCK:
            if _WORKFLOW_EXECUTOR is None or getattr(_WORKFLOW_EXECUTOR, "_shutdown", False):
                _WORKFLOW_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
                    max_workers=max_workers or 4,
                    thread_name_prefix="WorkflowSyncRunner",
                )
    return _WORKFLOW_EXECUTOR


def shutdown_workflow_executor(wait: bool = False) -> None:
    """
    Cleanly terminate the reusable workflow ThreadPoolExecutor.
    """
    global _WORKFLOW_EXECUTOR
    with _EXECUTOR_LOCK:
        if _WORKFLOW_EXECUTOR is not None:
            _WORKFLOW_EXECUTOR.shutdown(wait=wait)
            _WORKFLOW_EXECUTOR = None


atexit.register(shutdown_workflow_executor)


def run_workflow_sync(
    user_query: str,
    user_token: Optional[str] = None,
    company_id: Optional[int] = None,
    thread_id: Optional[str] = None,
    graph: Optional[Any] = None,
    on_node_finish: Optional[Any] = None,
) -> AgentState:
    """
    Synchronously run the compiled workflow graph (safe for CLI and test runners).
    Handles environments with or without an active asyncio event loop.
    Reuses a cached module-level ThreadPoolExecutor when called within an active loop.
    Supports optional on_node_finish callback for live step-by-step progress tracking.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if on_node_finish:
        async def _run_streaming():
            acc: AgentState = {}
            async for node_name, update, acc_state in astream_workflow_steps(
                user_query=user_query,
                user_token=user_token,
                company_id=company_id,
                thread_id=thread_id,
                graph=graph,
            ):
                on_node_finish(node_name, update, acc_state)
                acc = acc_state
            return acc
        coro = _run_streaming()
    else:
        coro = arun_workflow(
            user_query=user_query,
            user_token=user_token,
            company_id=company_id,
            thread_id=thread_id,
            graph=graph,
        )

    if loop and loop.is_running():
        executor = get_workflow_executor()
        return executor.submit(asyncio.run, coro).result()
    else:
        return asyncio.run(coro)

