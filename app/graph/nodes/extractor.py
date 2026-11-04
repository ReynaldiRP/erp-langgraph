"""
Extractor Node for FastPrint ERP LangGraph Agent Workflow (TASK-4.2).
Uses an extensible schema registry (TOOL_SCHEMA_REGISTRY) to extract typed parameters
from natural language queries using LLM structured output and temporal date resolution.
"""
import calendar
from datetime import date, timedelta
import os
import re
import sys
from typing import Any, Dict, Optional, Tuple, Type
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import SystemMessage, HumanMessage
from pydantic import BaseModel

from app.graph.state import AgentState
from app.schemas.tools import (
    AccountBalanceFilterSchema,
    JournalEntriesFilterSchema,
    StockLevelFilterSchema,
    ProductInfoFilterSchema,
    VendorInvoiceFilterSchema,
)
from app.services.llm import get_chat_model

# Extensible registry mapping tool names to input filter schemas
TOOL_SCHEMA_REGISTRY: Dict[str, Type[BaseModel]] = {
    "fetch_vendor_invoices": VendorInvoiceFilterSchema,
    "get_journal_entries": JournalEntriesFilterSchema,
    "get_account_balance": AccountBalanceFilterSchema,
    "get_stock_level": StockLevelFilterSchema,
    "get_product_info": ProductInfoFilterSchema,
}


def resolve_relative_dates(
    text: str,
    ref_date: Optional[date] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """
    Resolve relative Indonesian temporal expressions into ISO YYYY-MM-DD date range.

    Supported phrases:
    - 'bulan ini': 1st day of current month to last day of current month
    - 'bulan lalu': 1st day of previous month to last day of previous month
    - 'hari ini': current date
    - 'kemarin': previous day
    - 'minggu ini': Monday to Sunday of the current week
    - Explicit dates: YYYY-MM-DD format
    """
    today = ref_date or date.today()
    cleaned = text.lower()

    # 1. Check for explicit YYYY-MM-DD dates
    explicit_dates = re.findall(r"\b(\d{4}-\d{2}-\d{2})\b", cleaned)
    if len(explicit_dates) >= 2:
        return explicit_dates[0], explicit_dates[1]
    elif len(explicit_dates) == 1:
        return explicit_dates[0], None

    # 2. Bulan ini (current month)
    if "bulan ini" in cleaned:
        first_day = today.replace(day=1).strftime("%Y-%m-%d")
        _, last_day_num = calendar.monthrange(today.year, today.month)
        last_day = today.replace(day=last_day_num).strftime("%Y-%m-%d")
        return first_day, last_day

    # 3. Bulan lalu (previous month)
    if "bulan lalu" in cleaned or "bulan kemarin" in cleaned:
        first_current = today.replace(day=1)
        prev_month_end = first_current - timedelta(days=1)
        first_day = prev_month_end.replace(day=1).strftime("%Y-%m-%d")
        last_day = prev_month_end.strftime("%Y-%m-%d")
        return first_day, last_day

    # 4. Hari ini (today)
    if "hari ini" in cleaned:
        today_str = today.strftime("%Y-%m-%d")
        return today_str, today_str

    # 5. Kemarin (yesterday)
    if "kemarin" in cleaned:
        yesterday_str = (today - timedelta(days=1)).strftime("%Y-%m-%d")
        return yesterday_str, yesterday_str

    # 6. Minggu ini (current week)
    if "minggu ini" in cleaned:
        start_week = today - timedelta(days=today.weekday())
        end_week = start_week + timedelta(days=6)
        return start_week.strftime("%Y-%m-%d"), end_week.strftime("%Y-%m-%d")

    return None, None


def _fallback_extract_vendor_invoice(
    query: str,
    ref_date: Optional[date] = None,
) -> Dict[str, Any]:
    """Deterministic heuristic extraction for vendor invoice inquiries."""
    params: Dict[str, Any] = {}

    # Extract vendor name (e.g. 'vendor PT Sumber Makmur', 'PT Sumber Makmur', 'supplier Toko Kertas')
    vendor_pattern = re.search(
        r"(?:vendor|supplier|pemasok)\s+([A-Za-z0-9\.\s]+?)(?:\s+(?:bulan|hari|tanggal|minggu|tahun|status|sebesar|di|pada)|$)",
        query,
        re.IGNORECASE,
    )
    if vendor_pattern:
        params["vendor_name"] = vendor_pattern.group(1).strip()
    else:
        # Check for company prefixes like PT, CV, UD
        prefix_pattern = re.search(
            r"\b((?:PT|CV|UD|Toko)\s+[A-Za-z0-9\s]+?)(?:\s+(?:bulan|hari|tanggal|minggu|status)|$)",
            query,
            re.IGNORECASE,
        )
        if prefix_pattern:
            params["vendor_name"] = prefix_pattern.group(1).strip()

    # Extract relative dates
    date_from, date_to = resolve_relative_dates(query, ref_date)
    if date_from:
        params["date_from"] = date_from
    if date_to:
        params["date_to"] = date_to

    return params


def extractor_node(
    state: AgentState,
    llm: Optional[BaseChatModel] = None,
    ref_date: Optional[date] = None,
) -> Dict[str, Any]:
    """
    LangGraph pipeline node that extracts structured tool filter parameters from user query.

    Args:
        state: Current AgentState.
        llm: Optional BaseChatModel instance with structured output capabilities.
        ref_date: Reference anchor date for relative date resolution (defaults to date.today()).

    Returns:
        Partial AgentState update dictionary with:
            - extracted_params: Dictionary of parsed and validated parameter filters.
    """
    user_query = state.get("user_query")
    if not user_query and state.get("messages"):
        last_msg = state["messages"][-1]
        user_query = getattr(last_msg, "content", "") or ""

    user_query = (user_query or "").strip()
    detected_tool = state.get("detected_tool")
    retry_count = int(state.get("retry_count") or 0)
    retry_instruction = state.get("retry_instruction")
    today = ref_date or date.today()

    # If no tool was detected by router, return empty extracted params
    if not detected_tool:
        return {"extracted_params": {}}

    target_schema = TOOL_SCHEMA_REGISTRY.get(detected_tool)
    if target_schema is None:
        # Fallback to general journal entries if unknown tool under accounting
        if state.get("detected_domain") == "accounting":
            target_schema = JournalEntriesFilterSchema
        else:
            return {"extracted_params": {}}

    # Pre-calculate relative dates
    rel_date_from, rel_date_to = resolve_relative_dates(user_query, today)

    # Attempt LLM structured output extraction
    model = llm
    is_test_env = "unittest" in sys.modules or os.environ.get("FASTPRINT_TEST_ENV") == "true"
    if model is None and not is_test_env:
        try:
            model = get_chat_model()
        except Exception:
            model = None

    if model is not None:
        try:
            _, last_day_num = calendar.monthrange(today.year, today.month)
            month_start = today.replace(day=1).strftime("%Y-%m-%d")
            month_end = today.replace(day=last_day_num).strftime("%Y-%m-%d")

            system_prompt = (
                f"You are an expert ERP Parameter Extractor for FastPrint ERP.\n"
                f"Target Tool: {detected_tool}\n"
                f"Reference Today's Date: {today.strftime('%Y-%m-%d')}\n"
                f"Current Month Range: {month_start} to {month_end}\n\n"
                f"Extract filter entities strictly adhering to the schema.\n"
                f"If the user says 'bulan ini', set date_from='{month_start}' and date_to='{month_end}'."
            )

            if retry_instruction:
                system_prompt += (
                    f"\n\nIMPORTANT RETRY INSTRUCTION (Attempt {retry_count}):\n"
                    f"{retry_instruction}\n"
                    f"Adjust and relax your extracted filter parameters accordingly."
                )

            structured_model = model.with_structured_output(target_schema)
            messages = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_query),
            ]
            extracted_obj = structured_model.invoke(messages)

            if isinstance(extracted_obj, BaseModel):
                extracted_dict = extracted_obj.model_dump(exclude_none=True)
            elif isinstance(extracted_obj, dict):
                extracted_dict = extracted_obj
            else:
                extracted_dict = {}

            # Backfill relative dates only on initial attempt (retry_count == 0)
            if retry_count == 0:
                if rel_date_from and not extracted_dict.get("date_from"):
                    extracted_dict["date_from"] = rel_date_from
                if rel_date_to and not extracted_dict.get("date_to"):
                    extracted_dict["date_to"] = rel_date_to

            return {"extracted_params": extracted_dict}
        except Exception:
            pass

    # Deterministic fallback when offline or LLM unavailable
    if detected_tool == "fetch_vendor_invoices":
        params = _fallback_extract_vendor_invoice(user_query, today)
        if retry_count == 1:
            # Staged relaxation attempt 1: relax date filters
            params.pop("date_from", None)
            params.pop("date_to", None)
        elif retry_count >= 2:
            # Staged relaxation attempt 2: relax both date and vendor/search filters
            params.pop("date_from", None)
            params.pop("date_to", None)
            params.pop("vendor_name", None)
            params.pop("search", None)
        return {"extracted_params": params}

    # Generic fallback: at minimum resolve dates if initial attempt
    params = {}
    if retry_count == 0:
        if rel_date_from:
            params["date_from"] = rel_date_from
        if rel_date_to:
            params["date_to"] = rel_date_to

    return {"extracted_params": params}
