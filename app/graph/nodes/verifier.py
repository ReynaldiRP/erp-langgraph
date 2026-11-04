"""
Data Verification Node for FastPrint ERP LangGraph Agent Workflow (TASK-4.4).
Evaluates tool execution results, implements an automated retry/correction cycle
for empty query results (with maximum 2 retries), and provides conditional edge routing.
"""
import logging
from typing import Any, Dict, List, Optional, Union

from app.graph.state import AgentState, VerificationStatus

logger = logging.getLogger("verifier_node")


def _calculate_records_count(tool_result: Any) -> int:
    """
    Hierarchically inspect and extract the record count from diverse ERP response structures.

    Handles:
    - total_count or count fields (e.g. Journal entries, Vendor invoices)
    - list entries/records (e.g. accounts, entries, products, items)
    - root lists (e.g. multiple items array)
    - single record dictionary (e.g. stock level for a single product)
    - empty structures or None -> 0
    """
    if tool_result is None:
        return 0

    if isinstance(tool_result, list):
        return len(tool_result)

    if isinstance(tool_result, dict):
        if not tool_result:
            return 0

        # Check explicit count fields
        if "total_count" in tool_result and isinstance(tool_result["total_count"], (int, float)):
            return int(tool_result["total_count"])

        if "count" in tool_result and isinstance(tool_result["count"], (int, float)):
            return int(tool_result["count"])

        # Check common collection list keys
        for list_key in ("entries", "accounts", "products", "items", "records", "lines"):
            val = tool_result.get(list_key)
            if isinstance(val, list):
                return len(val)

        # Nested data field inspection (if envelope wasn't stripped)
        if "data" in tool_result and isinstance(tool_result["data"], (dict, list)):
            return _calculate_records_count(tool_result["data"])

        # Single record payload (e.g. stock level dict containing product_id or qty_on_hand)
        return 1

    return 0


def verifier_node(state: AgentState) -> Dict[str, Any]:
    """
    LangGraph pipeline node that evaluates state['tool_result'] to verify data availability.

    Rules (TASK-4.4):
    1. If tool execution previously encountered an ERROR, preserve ERROR status.
    2. If records_count > 0: Set verification_status = "VALID".
    3. If records_count == 0 and retry_count < 2:
       Set verification_status = "RETRY", increment retry_count += 1,
       and provide staged filter correction instruction in retry_instruction.
    4. If records_count == 0 and retry_count >= 2:
       Set verification_status = "EMPTY".

    Args:
        state: Current AgentState.

    Returns:
        Partial AgentState update dictionary.
    """
    # 1. Preserve pre-existing ERROR status from tool_node
    if state.get("verification_status") == "ERROR" or (
        state.get("error_message") and state.get("tool_result") is None
    ):
        logger.info("Preserving ERROR verification status from previous node failure.")
        return {
            "verification_status": "ERROR",
            "retry_instruction": None,
        }

    tool_result = state.get("tool_result")
    records_count = _calculate_records_count(tool_result)
    current_retry_count = int(state.get("retry_count") or 0)

    # 2. Valid data found
    if records_count > 0:
        logger.info(f"Verification successful: {records_count} record(s) found.")
        return {
            "verification_status": "VALID",
            "retry_instruction": None,
        }

    # 3. Empty data with remaining retry budget (retry_count < 2)
    if current_retry_count < 2:
        next_retry = current_retry_count + 1
        if next_retry == 1:
            instruction = (
                "Hasil query kosong (0 data). Longgarkan atau hilangkan batasan filter tanggal "
                "(date_from / date_to) untuk memperluas pencarian transaksi."
            )
        else:
            instruction = (
                "Hasil query masih kosong setelah pelonggaran tanggal. Hapus batasan pencarian "
                "kata kunci / nama vendor yang terlalu spesifik untuk memeriksa transaksi umum."
            )

        logger.warning(
            f"Verification empty (records_count=0). Triggering RETRY {next_retry}/2 with instruction: {instruction}"
        )
        return {
            "verification_status": "RETRY",
            "retry_count": next_retry,
            "retry_instruction": instruction,
        }

    # 4. Empty data and retry limit reached (retry_count >= 2)
    logger.info("Verification finished: 0 records found and retry limit (2x) reached -> EMPTY.")
    return {
        "verification_status": "EMPTY",
        "retry_instruction": None,
    }


def decide_after_verification(state: AgentState) -> str:
    """
    Conditional edge routing helper function.

    Routing rules:
    - Returns 'extractor' if verification_status == 'RETRY'.
    - Returns 'synthesizer' if verification_status in ('VALID', 'EMPTY', 'ERROR').

    Args:
        state: Current AgentState.

    Returns:
        Target node identifier name ('extractor' or 'synthesizer').
    """
    status: Optional[VerificationStatus] = state.get("verification_status")

    if status == "RETRY":
        return "extractor"

    # Default to synthesizer for VALID, EMPTY, or ERROR
    return "synthesizer"
