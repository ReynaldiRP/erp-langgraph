"""
Router Node for FastPrint ERP LangGraph Agent Workflow (TASK-4.2).
Provides high-speed hybrid routing: keyword/regex pattern matching for ERP modules
with negative guardrails (preventing customer-vendor confusion) and LLM fallback.
"""
import re
from typing import Any, Dict, Literal, Optional
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import SystemMessage, HumanMessage
from pydantic import BaseModel, Field

from app.graph.state import AgentState
from app.services.llm import get_chat_model


class RouterDecision(BaseModel):
    """Structured output schema for LLM router fallback."""
    domain: Literal["accounting", "inventory", "general"] = Field(
        ...,
        description="Target ERP module domain: 'accounting', 'inventory', or 'general'."
    )
    tool: Optional[Literal[
        "fetch_vendor_invoices",
        "get_account_balance",
        "get_journal_entries",
        "get_stock_level",
        "get_product_info",
    ]] = Field(
        None,
        description="Target tool name to invoke in ERP backend, or null if general / unmapped."
    )
    reasoning: Optional[str] = Field(
        None,
        description="Brief explanation of why this domain and tool were selected."
    )


# Disambiguation term sets
CUSTOMER_TERMS = {"pelanggan", "customer", "piutang", "penjualan", "sales", "buyer", "klien"}
VENDOR_TERMS = {"vendor", "supplier", "pemasok", "hutang", "pembelian", "beli"}

ACCOUNTING_BALANCE_PATTERNS = [
    r"\bsaldo\b",
    r"\bbalance\b",
    r"\bkas\b",
    r"\bkas kecil\b",
    r"\bneraca\b",
    r"\bbuku kas\b",
    r"\bsaldo akun\b",
]

ACCOUNTING_JOURNAL_PATTERNS = [
    r"\bjurnal\b",
    r"\bjournal\b",
    r"\bbuku besar\b",
    r"\bledger\b",
    r"\bentri jurnal\b",
    r"\bdebit kredit\b",
]

INVENTORY_STOCK_PATTERNS = [
    r"\bstok\b",
    r"\bstock\b",
    r"\bsisa barang\b",
    r"\bsisa stok\b",
    r"\bgudang\b",
    r"\bkuantitas\b",
    r"\bqty\b",
    r"\bon hand\b",
]

INVENTORY_PRODUCT_PATTERNS = [
    r"\bproduk\b",
    r"\bproduct\b",
    r"\bharga barang\b",
    r"\bharga jual\b",
    r"\bharga beli\b",
    r"\bspesifikasi\b",
    r"\bdetail barang\b",
    r"\bsku\b",
    r"\bbarcode\b",
    r"\bvarian\b",
]


def _match_any(patterns: list, text: str) -> bool:
    """Check if any regex pattern matches the text."""
    for pattern in patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False


def classify_query_rule_based(query: str) -> Optional[Dict[str, Any]]:
    """
    Deterministic rule-based classifier for ERP queries.
    Returns dictionary with detected_domain and detected_tool if a definitive match is found,
    or None if ambiguous.
    """
    cleaned = query.lower()
    has_customer = any(term in cleaned for term in CUSTOMER_TERMS)
    has_vendor = any(term in cleaned for term in VENDOR_TERMS)

    # 1. Negative Guardrail: Disambiguate Customer vs Vendor
    if has_customer and not has_vendor:
        # User explicitly asks about customer invoices/bills (e.g. "Cek tagihan pelanggan PT Sumber Makmur")
        # Guardrail: NEVER trigger vendor invoice flow!
        return {
            "detected_domain": "general",
            "detected_tool": None,
        }

    # 2. Vendor Invoice Check (Accounting Domain)
    # Triggered by keywords like "tagihan vendor", "faktur supplier", "invoice vendor", etc.
    if has_vendor and ("tagihan" in cleaned or "faktur" in cleaned or "invoice" in cleaned or "bill" in cleaned or "hutang" in cleaned):
        return {
            "detected_domain": "accounting",
            "detected_tool": "fetch_vendor_invoices",
        }

    if has_vendor:
        # General vendor lookup without explicit balance
        return {
            "detected_domain": "accounting",
            "detected_tool": "fetch_vendor_invoices",
        }

    # 3. Account Balance Check (Accounting Domain)
    if _match_any(ACCOUNTING_BALANCE_PATTERNS, cleaned):
        return {
            "detected_domain": "accounting",
            "detected_tool": "get_account_balance",
        }

    # 4. Journal Entries Check (Accounting Domain)
    if _match_any(ACCOUNTING_JOURNAL_PATTERNS, cleaned):
        return {
            "detected_domain": "accounting",
            "detected_tool": "get_journal_entries",
        }

    # 5. Stock Level Check (Inventory Domain)
    if _match_any(INVENTORY_STOCK_PATTERNS, cleaned):
        return {
            "detected_domain": "inventory",
            "detected_tool": "get_stock_level",
        }

    # 6. Product Info Check (Inventory Domain)
    if _match_any(INVENTORY_PRODUCT_PATTERNS, cleaned):
        return {
            "detected_domain": "inventory",
            "detected_tool": "get_product_info",
        }

    return None


def router_node(
    state: AgentState,
    llm: Optional[BaseChatModel] = None,
) -> Dict[str, Any]:
    """
    LangGraph pipeline node that determines the domain and target tool from user query.

    Args:
        state: The current AgentState dictionary.
        llm: Optional ChatModel instance for fallback classification.

    Returns:
        Partial AgentState update dictionary with:
            - detected_domain: "accounting", "inventory", or "general"
            - detected_tool: target tool name string or None
    """
    user_query = state.get("user_query")
    if not user_query and state.get("messages"):
        last_msg = state["messages"][-1]
        user_query = getattr(last_msg, "content", "") or ""

    user_query = (user_query or "").strip()
    if not user_query:
        return {
            "detected_domain": "general",
            "detected_tool": None,
        }

    # Step 1: Fast Rule / Regex Pattern Matching
    rule_result = classify_query_rule_based(user_query)
    if rule_result is not None:
        return rule_result

    # Step 2: Fallback to LLM Structured Output
    model = llm
    if model is None:
        try:
            model = get_chat_model()
        except Exception:
            # If no API key configured in test/offline environment, safely fallback
            model = None

    if model is not None:
        try:
            system_prompt = (
                "You are an ERP Domain and Tool Router for FastPrint ERP.\n"
                "Classify user inquiries into target module ('accounting', 'inventory', 'general') "
                "and select the most appropriate tool.\n"
                "CRITICAL GUARDRAIL: Do NOT route customer / pelanggan inquiries to 'fetch_vendor_invoices'. "
                "Vendor invoices strictly apply to suppliers and purchase bills."
            )
            structured_model = model.with_structured_output(RouterDecision)
            messages = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_query),
            ]
            decision: RouterDecision = structured_model.invoke(messages)
            return {
                "detected_domain": decision.domain,
                "detected_tool": decision.tool,
            }
        except Exception:
            pass

    # Default fallback when unclassified
    return {
        "detected_domain": "general",
        "detected_tool": None,
    }
