"""
Tool Execution Node for FastPrint ERP LangGraph Agent Workflow (TASK-4.3).
Executes ERP tools via DjangoCoreClient using dynamic dispatching, multi-company prioritization,
graceful error capture, and preserves transaction data and calculations block intact.
"""
import asyncio
import concurrent.futures
import logging
from typing import Any, Dict, Optional

from app.core.config import settings
from app.core.context import sanitize_erp_payload
from app.graph.state import AgentState
from app.services.django_client import (
    DjangoCoreClient,
    DjangoCoreClientError,
)

logger = logging.getLogger("tool_node")


async def tool_node(
    state: AgentState,
    client: Optional[DjangoCoreClient] = None,
) -> Dict[str, Any]:
    """
    Asynchronous LangGraph node that executes ERP tools based on state parameters and routing.

    Definition of Done:
    - Passes user_token and company_id to Django Core.
    - Preserves transaction data and the calculations block intact in state['tool_result'].
    - Captures errors gracefully into state['error_message'] and sets verification_status='ERROR'.

    Args:
        state: Current AgentState containing extracted_params, user_token, company_id, etc.
        client: Optional DjangoCoreClient instance (if None, initializes default client).

    Returns:
        Partial AgentState update dictionary with:
            - tool_result: Complete response dictionary from ERP (or None if error)
            - verification_status: 'VALID' on success, 'ERROR' on failure
            - error_message: Error string if an exception occurred, else None
    """
    extracted_params = dict(state.get("extracted_params") or {})
    user_token = state.get("user_token")
    detected_tool = state.get("detected_tool") or "fetch_vendor_invoices"

    # Multi-Company Scoping Priority:
    # Query-extracted company_id takes precedence over active session state company_id
    target_company_id = extracted_params.get("company_id") or state.get("company_id")

    own_client = client is None
    erp_client = client or DjangoCoreClient()

    try:
        # Adapt parameters for vendor invoices: map vendor_name to search if search is unset
        if detected_tool in ("fetch_vendor_invoices", "get_journal_entries"):
            if "vendor_name" in extracted_params and "search" not in extracted_params:
                extracted_params["search"] = extracted_params["vendor_name"]

        # Dynamic Tool Dispatch Registry
        dispatch_map = {
            "fetch_vendor_invoices": erp_client.fetch_vendor_invoices,
            "get_journal_entries": erp_client.fetch_journal_entries,
            "get_account_balance": erp_client.fetch_account_balance,
            "get_stock_level": erp_client.fetch_stock_level,
            "get_product_info": erp_client.fetch_product_info,
            "get_locations": erp_client.fetch_locations,
        }

        # Sequential 2-Step Execution: If checking stock level with location_name, resolve location_id first!
        if detected_tool == "get_stock_level" and extracted_params.get("location_name") and not extracted_params.get("location_id"):
            try:
                loc_res = await erp_client.fetch_locations(
                    params={"search": extracted_params["location_name"]},
                    user_token=user_token,
                    company_id=target_company_id,
                )
                locations = loc_res.get("locations", []) if isinstance(loc_res, dict) else (loc_res if isinstance(loc_res, list) else [])
                if locations:
                    matched_loc = locations[0]
                    extracted_params["location_id"] = matched_loc.get("id")
                    extracted_params["matched_location_name"] = matched_loc.get("name")
            except Exception as loc_err:
                logger.warning(f"Could not resolve location_name '{extracted_params.get('location_name')}': {loc_err}")

        handler = dispatch_map.get(detected_tool, erp_client.fetch_vendor_invoices)
        raw_result = await handler(
            params=extracted_params,
            user_token=user_token,
            company_id=target_company_id,
        )

        # Attach resolved location_name to raw_result if applicable
        if isinstance(raw_result, dict):
            loc_label = extracted_params.get("matched_location_name") or extracted_params.get("location_name")
            if loc_label and not raw_result.get("location_name"):
                raw_result["location_name"] = loc_label

        # Context Pruning: Sanitize technical Django metadata if enabled
        final_tool_result = (
            sanitize_erp_payload(raw_result)
            if settings.ENABLE_PAYLOAD_SANITIZATION
            else raw_result
        )

        # Store pruned and verified response data intact into tool_result (DoD)
        return {
            "tool_result": final_tool_result,
            "verification_status": "VALID",
            "error_message": None,
        }

    except DjangoCoreClientError as err:
        logger.warning(f"DjangoCoreClientError in tool_node for tool '{detected_tool}': {err}")
        return {
            "tool_result": None,
            "verification_status": "ERROR",
            "error_message": str(err),
        }
    except Exception as exc:
        logger.error(f"Unexpected error in tool_node for tool '{detected_tool}': {exc}", exc_info=True)
        return {
            "tool_result": None,
            "verification_status": "ERROR",
            "error_message": f"Unexpected execution error: {str(exc)}",
        }
    finally:
        if own_client:
            await erp_client.aclose()


def tool_node_sync(
    state: AgentState,
    client: Optional[DjangoCoreClient] = None,
) -> Dict[str, Any]:
    """
    Synchronous wrapper for tool_node.
    Enables safe execution in synchronous CLI runners, testing suites, or standard scripts.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # Running inside an active event loop (e.g. async runner or Jupyter)
        with concurrent.futures.ThreadPoolExecutor() as executor:
            return executor.submit(asyncio.run, tool_node(state, client=client)).result()
    else:
        return asyncio.run(tool_node(state, client=client))
