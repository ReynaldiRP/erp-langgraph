"""
Router Node for FastPrint ERP LangGraph Agent Workflow (TASK-4.2).
Provides high-speed hybrid routing: keyword/regex pattern matching for ERP modules
with negative guardrails (preventing customer-vendor confusion) and LLM fallback.
"""
import os
import re
import sys
from typing import Any, Dict, Literal, Optional
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import SystemMessage, HumanMessage
from pydantic import BaseModel, Field, model_validator

from app.graph.state import AgentState
from app.services.llm import get_chat_model


class RouterDecision(BaseModel):
    """Structured output schema for LLM router fallback."""
    domain: Literal["accounting", "inventory", "knowledge", "general", "out_of_scope"] = Field(
        ...,
        description="Target ERP module domain: 'accounting', 'inventory', 'knowledge' (SOPs, specs, policies), 'general' (greetings/help), or 'out_of_scope' (unrelated queries outside ERP)."
    )
    query_mode: Literal["live_erp", "vector_rag", "hybrid", "general", "out_of_scope"] = Field(
        "live_erp",
        description="Execution mode: 'vector_rag' for SOP/policies/specs, 'live_erp' for real-time transactions/stock, 'hybrid' for combined questions, 'general', or 'out_of_scope'."
    )
    tool: Optional[Literal[
        "fetch_vendor_invoices",
        "get_account_balance",
        "get_journal_entries",
        "get_stock_level",
        "get_product_info",
        "get_locations",
    ]] = Field(
        None,
        description="Target tool name to invoke in ERP backend, or null if knowledge / general / out_of_scope."
    )
    rag_query: Optional[str] = Field(
        None,
        description="Extracted search query for RAG knowledge retrieval if query_mode is vector_rag or hybrid."
    )
    reasoning: Optional[str] = Field(
        None,
        description="Brief explanation of why this domain and tool were selected."
    )

    @model_validator(mode="after")
    def clean_tool_for_general_and_out_of_scope(self) -> "RouterDecision":
        """Enforce null tool whenever domain is general, out_of_scope, or knowledge."""
        if self.domain in ("general", "out_of_scope", "knowledge") or self.query_mode == "vector_rag":
            self.tool = None
        return self


# Disambiguation term sets
CUSTOMER_TERMS = {"pelanggan", "customer", "piutang", "penjualan", "sales", "buyer", "klien"}
VENDOR_TERMS = {"vendor", "supplier", "pemasok", "hutang", "pembelian", "beli"}

ACCOUNTING_BALANCE_PATTERNS = [
    r"\bsaldo(nya)?\b",
    r"\bbalance\b",
    r"\bkas\b",
    r"\bkas kecil\b",
    r"\bneraca\b",
    r"\bbuku kas\b",
    r"\bsaldo akun\b",
]

ACCOUNTING_JOURNAL_PATTERNS = [
    r"\bjurnal(nya)?\b",
    r"\bjournal\b",
    r"\bbuku besar\b",
    r"\bledger\b",
    r"\bentri jurnal\b",
    r"\bdebit kredit\b",
]

INVENTORY_STOCK_PATTERNS = [
    r"\bstok(nya)?\b",
    r"\bstock(nya)?\b",
    r"\bsisa barang(nya)?\b",
    r"\bsisa stok(nya)?\b",
    r"\bkuantitas(nya)?\b",
    r"\bqty\b",
    r"\bon hand\b",
]

INVENTORY_LOCATION_PATTERNS = [
    r"\b(daftar|list|semua|info|data)\s+(gudang|lokasi|warehouse)\b",
    r"\b(daftar|list)\s+(gudang|lokasi|warehouse)\b",
    r"\bada\s+(gudang|lokasi|warehouse)\b",
    r"\b(gudang|lokasi|warehouse)\s+(apa\s+saja|tersedia|aktif)\b",
    r"^(gudang|lokasi|warehouse)\b",
]

INVENTORY_PRODUCT_PATTERNS = [
    r"\bproduk(nya)?\b",
    r"\bproduct\b",
    r"\bharga barang\b",
    r"\bharga jual\b",
    r"\bharga beli\b",
    r"\bdetail barang\b",
    r"\bsku\b",
    r"\bbarcode\b",
    r"\bvarian\b",
]

KNOWLEDGE_RAG_PATTERNS = [
    r"\bsop\b",
    r"\bkebijakan\b",
    r"\bprosedur\b",
    r"\baturan\b",
    r"\b(per)?syarat(an)?\b",
    r"\b(ke)?tentuan\b",
    r"\b(di|me)?retur(kan)?(nya)?\b",
    r"\b(ber)?garansi\b",
    r"\bspesifikasi\b",
    r"\b(gsm|gramasi|ketebalan)\b",
    r"\bprofil\s+(vendor|supplier|customer|pelanggan)\b",
    r"\btermin\s+(pembayaran|kredit)\b",
    r"\bcara\s+(klaim|retur|pengajuan)\b",
    r"\b(di)?klaim\b",
]

GREETING_PATTERNS = [
    r"^(halo|hai|hi|hey|hei|helo|hello)\b",
    r"^(selamat\s+(pagi|siang|sore|malam|petang))\b",
    r"^(assalamu\s*['’]?alaikum|sampurasun|kulonuwun)\b",
    r"^(ping|p\b|tes|test|testing)\b",
    r"^(terima\s*kasih|makasih|thanks|thank\s*you|matur\s*nuwun)\b",
]

GENERAL_INQUIRY_PATTERNS = [
    r"^(bisa\s+bantu\s+apa|kamu\s+siapa|fitur\s+apa|menu|help|bantuan|siapa\s+kamu|apa\s+yang\s+bisa\s+kamu\s+lakukan)\b",
]

OUT_OF_SCOPE_PATTERNS = [
    r"\b(system\s*design|arsitektur\s*(sistem)?|source\s*code|prompt\s*(kamu|anda)|system\s*prompt)\b",
    r"\b(buatkan\s+(puisi|cerita|kode|script)|ceritakan\s+(lelucon|dongeng)|siapa\s+presiden|ramalan\s+cuaca)\b",
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
    Returns dictionary with detected_domain, detected_tool, query_mode, and rag_query
    if a definitive match is found, or None if ambiguous.
    """
    cleaned = query.lower().strip()
    has_customer = any(term in cleaned for term in CUSTOMER_TERMS)
    has_vendor = any(term in cleaned for term in VENDOR_TERMS)
    is_rag = _match_any(KNOWLEDGE_RAG_PATTERNS, cleaned)

    # 0. Sapaan / Percakapan Kasual / Bantuan Cepat
    if _match_any(GREETING_PATTERNS, cleaned) or _match_any(GENERAL_INQUIRY_PATTERNS, cleaned):
        has_erp_intent = (
            _match_any(ACCOUNTING_BALANCE_PATTERNS, cleaned)
            or _match_any(ACCOUNTING_JOURNAL_PATTERNS, cleaned)
            or _match_any(INVENTORY_STOCK_PATTERNS, cleaned)
            or _match_any(INVENTORY_LOCATION_PATTERNS, cleaned)
            or _match_any(INVENTORY_PRODUCT_PATTERNS, cleaned)
            or has_vendor
            or is_rag
        )
        if not has_erp_intent:
            return {
                "detected_domain": "general",
                "detected_tool": None,
                "query_mode": "general",
                "rag_query": None,
            }

    # 0.1 Fast-Path Out-of-Scope Guardrail
    if _match_any(OUT_OF_SCOPE_PATTERNS, cleaned):
        return {
            "detected_domain": "out_of_scope",
            "detected_tool": None,
            "query_mode": "out_of_scope",
            "rag_query": None,
        }

    # 0.2 Identify potential Live ERP tool match
    live_domain: Optional[str] = None
    live_tool: Optional[str] = None

    if has_vendor and ("tagihan" in cleaned or "faktur" in cleaned or "invoice" in cleaned or "bill" in cleaned or "hutang" in cleaned):
        live_domain = "accounting"
        live_tool = "fetch_vendor_invoices"
    elif _match_any(ACCOUNTING_BALANCE_PATTERNS, cleaned):
        live_domain = "accounting"
        live_tool = "get_account_balance"
    elif _match_any(ACCOUNTING_JOURNAL_PATTERNS, cleaned):
        live_domain = "accounting"
        live_tool = "get_journal_entries"
    elif _match_any(INVENTORY_STOCK_PATTERNS, cleaned):
        live_domain = "inventory"
        live_tool = "get_stock_level"
    elif _match_any(INVENTORY_LOCATION_PATTERNS, cleaned):
        live_domain = "inventory"
        live_tool = "get_locations"
    elif _match_any(INVENTORY_PRODUCT_PATTERNS, cleaned) and not is_rag:
        live_domain = "inventory"
        live_tool = "get_product_info"
    elif has_vendor and not is_rag:
        live_domain = "accounting"
        live_tool = "fetch_vendor_invoices"

    # 1. Negative Guardrail: Customer Invoice / Billing Inquiries
    if has_customer and not has_vendor and not is_rag:
        return {
            "detected_domain": "general",
            "detected_tool": None,
            "query_mode": "general",
            "rag_query": None,
        }

    # 2. Check for Hybrid (both RAG knowledge and Live tool intent)
    if is_rag and live_tool:
        return {
            "detected_domain": live_domain,
            "detected_tool": live_tool,
            "query_mode": "hybrid",
            "rag_query": query,
        }

    # 3. Check for Pure Vector RAG (SOP, specs, policies)
    if is_rag and not live_tool:
        return {
            "detected_domain": "knowledge",
            "detected_tool": None,
            "query_mode": "vector_rag",
            "rag_query": query,
        }

    # 4. Pure Live ERP tool match
    if live_tool:
        return {
            "detected_domain": live_domain,
            "detected_tool": live_tool,
            "query_mode": "live_erp",
            "rag_query": None,
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
    is_test_env = "unittest" in sys.modules or os.environ.get("FASTPRINT_TEST_ENV") == "true"
    if model is None and not is_test_env:
        try:
            model = get_chat_model()
        except Exception:
            # If no API key configured in test/offline environment, safely fallback
            model = None

    if model is not None:
        try:
            system_prompt = (
                "You are an ERP Domain, Tool, and RAG Knowledge Router for FastPrint ERP.\n"
                "Classify user inquiries into target module ('accounting', 'inventory', 'knowledge', 'general', 'out_of_scope') "
                "and determine the execution query_mode ('live_erp', 'vector_rag', 'hybrid', 'general', 'out_of_scope').\n\n"
                "ROUTING RULES:\n"
                "1. 'vector_rag' / domain='knowledge': Pure qualitative questions about SOPs, return policies, product technical specifications (GSM, size), warranty, or vendor contract terms. tool=null.\n"
                "2. 'live_erp': Numerical / real-time inquiries about live stock on hand, cash balances, journal ledger, or vendor bills/invoices.\n"
                "3. 'hybrid': Combined queries asking about both rules/SOP/specs AND live numbers/stock (e.g. 'Can we return Art Paper 210 and how many are in stock?'). Set detected_tool to the live ERP tool.\n"
                "4. 'general': Pure greetings or asking about FastPrint ERP capabilities.\n"
                "5. 'out_of_scope': Inquiries unrelated to FastPrint ERP data or operational records.\n\n"
                "CRITICAL GUARDRAILS:\n"
                "- Customer / pelanggan inquiries must NOT be routed to 'fetch_vendor_invoices'. Set domain='general' and tool=null.\n"
                "- Whenever domain is 'general' or 'out_of_scope', tool MUST be null."
            )
            structured_model = model.with_structured_output(RouterDecision)
            messages = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_query),
            ]
            decision: RouterDecision = structured_model.invoke(messages)
            detected_tool = decision.tool if decision.domain not in ("general", "out_of_scope", "knowledge") else None
            return {
                "detected_domain": decision.domain,
                "detected_tool": detected_tool,
                "query_mode": decision.query_mode,
                "rag_query": decision.rag_query or (user_query if decision.query_mode in ("vector_rag", "hybrid") else None),
            }
        except Exception:
            pass

    # Default fallback when unclassified
    return {
        "detected_domain": "general",
        "detected_tool": None,
        "query_mode": "general",
        "rag_query": None,
    }
