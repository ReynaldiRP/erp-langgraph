"""
Extractor Node for FastPrint ERP LangGraph Agent Workflow (TASK-4.2 / Milestone 2).
Uses an extensible schema registry (TOOL_SCHEMA_REGISTRY) to extract typed parameters
across multi-tool active_tools subagent plans with LLM structured output, temporal
date resolution, 3-tier entity resolution, and deterministic heuristic fallbacks.
"""
import calendar
import copy
from datetime import date, timedelta
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple, Type
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import SystemMessage, HumanMessage
from pydantic import BaseModel

from app.core.config import settings
from app.core.context import trim_conversation_messages, build_contextual_prompt
from app.graph.state import AgentState, ToolCallPlan
from app.schemas.tools import (
    AccountBalanceFilterSchema,
    JournalEntriesFilterSchema,
    StockLevelFilterSchema,
    ProductInfoFilterSchema,
    VendorInvoiceFilterSchema,
    LocationsFilterSchema,
)
from app.services.llm import get_chat_model

# Extensible registry mapping tool names to input filter schemas
TOOL_SCHEMA_REGISTRY: Dict[str, Type[BaseModel]] = {
    "fetch_vendor_invoices": VendorInvoiceFilterSchema,
    "get_journal_entries": JournalEntriesFilterSchema,
    "get_account_balance": AccountBalanceFilterSchema,
    "get_stock_level": StockLevelFilterSchema,
    "get_product_info": ProductInfoFilterSchema,
    "get_locations": LocationsFilterSchema,
}

TOOL_DOMAIN_MAP: Dict[str, str] = {
    "fetch_vendor_invoices": "accounting",
    "get_account_balance": "accounting",
    "get_journal_entries": "accounting",
    "get_stock_level": "inventory",
    "get_product_info": "inventory",
    "get_locations": "inventory",
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
    - 'N hari terakhir': N days window ending today
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

    # 7. N hari terakhir (e.g. '3 hari terakhir', '7 hari terakhir')
    n_days_match = re.search(r"\b(\d+)\s+hari\s+(?:terakhir|kebelakang|lalu)\b", cleaned)
    if n_days_match:
        n_days = int(n_days_match.group(1))
        start_date = today - timedelta(days=max(0, n_days - 1))
        return start_date.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")

    return None, None


VENDOR_DELIMITERS = r"(?:bulan|hari|tanggal|minggu|tahun|status|sebesar|di|pada|yang|belum|sudah|lunas|semua|dibayar|terbayar|unpaid|paid|dan)"
VENDOR_NAME_STOPWORDS = ("semua", "yang", "belum", "sudah", "lunas", "ini", "itu", "ada", "draft", "posted")
VENDOR_PREFIXES = ("pt", "cv", "ud", "toko", "tb", "pd", "fa")


def is_vendor_name(name: Optional[str]) -> bool:
    """Check if candidate name matches vendor / corporate entity patterns."""
    if not name:
        return False
    cleaned = name.strip().lower()
    for p in VENDOR_PREFIXES:
        if cleaned == p or cleaned.startswith(p + " ") or cleaned.startswith(p + "."):
            return True
        if f" {p} " in f" {cleaned} " or f" {p}." in f" {cleaned}":
            return True
    return any(p in cleaned for p in ("vendor ", "supplier ", "pemasok "))


def is_account_code_or_name(name: Optional[str]) -> bool:
    """Check if candidate token is an account code or reserved financial keyword."""
    if not name:
        return False
    cleaned = name.strip().lower()
    if re.match(r"^\d{4,8}$", cleaned) and cleaned[0] in "123456":
        return True
    return cleaned in {"kas", "bank", "piutang", "hutang", "modal", "neraca", "biaya", "pendapatan", "saldo"}


def is_accounting_term(name: Optional[str]) -> bool:
    """Check if candidate token contains accounting / financial document keywords."""
    if not name:
        return False
    cleaned = name.strip().lower()
    return any(t in cleaned for t in ("faktur", "tagihan", "invoice", "jurnal", "saldo", "rekening", "neraca"))


ANAPHORA_TOKENS = {"itu", "tersebut", "tadi", "ini", "terkait", "dia", "nya"}
ANAPHORIC_CLITICS = (
    "stoknya", "harganya", "barangnya", "produknya", "saldonya",
    "tagihannya", "fakturnya", "invoicenya", "lokasinya", "detailnya",
    "transaksinya", "jurnalnya",
)


def has_anaphoric_reference(text: str) -> bool:
    """Check if text contains Indonesian anaphoric clitics, pronouns, or elliptical clauses."""
    cleaned = (text or "").lower()
    if any(clitic in cleaned for clitic in ANAPHORIC_CLITICS):
        return True
    if any(re.search(rf"\b{re.escape(token)}\b", cleaned) for token in ANAPHORA_TOKENS):
        return True
    if re.search(r"\b(?:berapa|gimana|bagaimana|mana|ada berapa)\s+(?:stok|harga|saldo|tagihan|faktur|detail)\b", cleaned):
        return True
    return False


def _fallback_extract_vendor_invoice(
    query: str,
    ref_date: Optional[date] = None,
) -> Dict[str, Any]:
    """Deterministic heuristic extraction for vendor invoice inquiries."""
    params: Dict[str, Any] = {}

    vendor_pattern = re.search(
        rf"(?:vendor|supplier|pemasok)\s+([A-Za-z0-9\.\s]+?)(?:\s+{VENDOR_DELIMITERS}|$)",
        query,
        re.IGNORECASE,
    )
    if vendor_pattern:
        v_name = vendor_pattern.group(1).strip()
        if v_name.lower() not in VENDOR_NAME_STOPWORDS:
            params["vendor_name"] = v_name
    else:
        prefix_pattern = re.search(
            rf"\b((?:PT|CV|UD|Toko)\.?\s+[A-Za-z0-9\.\s]+?)(?:\s+{VENDOR_DELIMITERS}|$)",
            query,
            re.IGNORECASE,
        )
        if prefix_pattern:
            v_name = prefix_pattern.group(1).strip()
            if v_name.lower() not in VENDOR_NAME_STOPWORDS:
                params["vendor_name"] = v_name

    query_lower = query.lower()
    if "belum lunas" in query_lower or "unpaid" in query_lower or "belum dibayar" in query_lower:
        params["status"] = "unpaid"
    elif "lunas" in query_lower or "paid" in query_lower or "sudah dibayar" in query_lower:
        params["status"] = "paid"

    date_from, date_to = resolve_relative_dates(query, ref_date)
    if date_from:
        params["date_from"] = date_from
    if date_to:
        params["date_to"] = date_to

    return params


STOCK_STOPWORDS = (
    "ini", "itu", "barang", "produk", "semua", "ada", "tersedia",
    "stok", "stock", "sisa",
    "di", "ke", "dari", "pada", "untuk", "terkait", "tentang",
    "gudang", "lokasi", "customer", "pelanggan",
    "dan", "serta", "juga", "faktur", "tagihan", "invoice", "jurnal", "saldo",
)


def _fallback_extract_stock_level(
    query: str,
    history: Optional[list] = None,
) -> Dict[str, Any]:
    """Deterministic heuristic extraction for stock level inquiries when LLM is unavailable."""
    params: Dict[str, Any] = {}
    cleaned = query.strip()

    loc_match = re.search(
        r"\bdi\s+(?:gudang\s+|lokasi\s+)?([A-Za-z0-9\_\-]+)",
        cleaned,
        re.IGNORECASE,
    )
    if loc_match:
        loc_candidate = loc_match.group(1).strip()
        if (
            loc_candidate.lower() not in ("mana", "sini", "situ", "kantor", "semua", "ini", "itu", "gudang", "lokasi")
            and loc_candidate.lower() not in STOCK_STOPWORDS
        ):
            params["location_name"] = loc_candidate

    match = re.search(
        r"(?:(?:stok|stock|sisa|barang|produk)\s+)+(?:untuk\s+|terkait\s+|dari\s+|tentang\s+)?([A-Za-z0-9\-\_\.]+)",
        cleaned,
        re.IGNORECASE,
    )
    if match:
        token = match.group(1).strip()
        if token.lower() not in STOCK_STOPWORDS:
            params["code"] = token
            return params

    tokens = re.findall(r"\b([A-Z0-9]+(?:-[A-Z0-9]+)+|\d{3,8})\b", cleaned)
    if tokens:
        params["code"] = tokens[0]
        return params

    if any(ref in cleaned.lower() for ref in ("itu", "tersebut", "tadi", "produk", "barang", "coba lagi", "ulang", "retry")) and history:
        for prev_msg in reversed(history):
            content = getattr(prev_msg, "content", "") or ""
            prev_tokens = re.findall(r"\b([A-Z0-9]+(?:-[A-Z0-9]+)+|\d{3,8})\b", content)
            if prev_tokens:
                params["code"] = prev_tokens[0]
                break

    return params


def _fallback_extract_locations(query: str) -> Dict[str, Any]:
    """Deterministic heuristic extraction for inventory location inquiries."""
    params: Dict[str, Any] = {}
    cleaned = query.strip()
    match = re.search(r"(?:gudang|lokasi|warehouse)\s+([A-Za-z0-9\_\-]+)", cleaned, re.IGNORECASE)
    if match:
        token = match.group(1).strip()
        if token.lower() not in ("apa", "saja", "semua", "yang", "ada", "aktif", "ini", "itu", "daftar", "list"):
            params["search"] = token
    return params


PRODUCT_STOPWORDS = {
    "berapa", "harga", "beli", "jual", "belinya", "jualnya", "cek", "info",
    "detail", "spesifikasi", "stok", "stoknya", "produk", "barang", "ada",
    "dan", "serta", "di", "ke", "dari", "pada", "untuk", "ini", "itu",
    "nya", "semua", "daftar", "list",
}


def _fallback_extract_product_info(
    query: str,
    history: Optional[list] = None,
) -> Dict[str, Any]:
    """Deterministic heuristic extraction for product info inquiries."""
    params: Dict[str, Any] = {}
    cleaned = query.strip()

    tokens = re.findall(r"\b([A-Z0-9]+(?:-[A-Z0-9]+)+|\d{4,8})\b", cleaned)
    if tokens:
        token = tokens[0]
        if not is_account_code_or_name(token) and not is_vendor_name(token):
            params["code"] = token
            return params

    m = re.search(
        r"(?:harga(?:\s+(?:beli|jual))?|detail\s+(?:produk|barang)?|info\s+(?:produk|barang)?|produk|barang|cari)\s+(?!dan\b|serta\b|di\b|ke\b|pada\b|untuk\b|faktur\b|tagihan\b|saldo\b|jurnal\b)([A-Za-z0-9\s\.\-]+?)(?:\s+(?:dan|serta|di|ke|pada|untuk|bulan|hari|berapa|status|faktur|tagihan|invoice|saldo|jurnal)|$)",
        cleaned,
        re.IGNORECASE,
    )
    if m:
        term = m.group(1).strip()
        words = [w for w in term.split() if w.lower() not in PRODUCT_STOPWORDS]
        if words:
            candidate = " ".join(words)
            if not is_vendor_name(candidate) and not is_account_code_or_name(candidate) and not is_accounting_term(candidate):
                params["search"] = candidate
                return params

    if any(ref in cleaned.lower() for ref in ("itu", "tersebut", "tadi", "produk", "barang", "coba lagi", "ulang", "retry")) and history:
        for prev_msg in reversed(history):
            content = getattr(prev_msg, "content", "") or ""
            prev_tokens = re.findall(r"\b([A-Z0-9]+(?:-[A-Z0-9]+)+|\d{4,8})\b", content)
            if prev_tokens:
                params["code"] = prev_tokens[0]
                break

    return params


def _fallback_extract_account_balance(
    query: str,
    ref_date: Optional[date] = None,
) -> Dict[str, Any]:
    """Deterministic heuristic extraction for account balance inquiries."""
    params: Dict[str, Any] = {}
    cleaned = query.strip()

    acc_codes = re.findall(r"\b([1-6]\d{3,7})\b", cleaned)
    if acc_codes:
        params["account_code"] = acc_codes[0]

    date_from, date_to = resolve_relative_dates(cleaned, ref_date)
    if date_from:
        params["date_from"] = date_from
    if date_to:
        params["date_to"] = date_to

    return params


def _fallback_extract_journal_entries(
    query: str,
    ref_date: Optional[date] = None,
) -> Dict[str, Any]:
    """Deterministic heuristic extraction for journal entries inquiries."""
    params: Dict[str, Any] = {}
    cleaned = query.strip()

    entry_match = re.search(r"\b(JE/\d{4}/\d+)\b", cleaned, re.IGNORECASE)
    if entry_match:
        params["entry_number"] = entry_match.group(1).upper()

    query_lower = cleaned.lower()
    if "draft" in query_lower:
        params["status"] = "draft"
    elif "posted" in query_lower or "post" in query_lower:
        params["status"] = "posted"
    elif "cancel" in query_lower or "batal" in query_lower:
        params["status"] = "cancel"

    if not params.get("entry_number"):
        m_search = re.search(
            r"(?:jurnal|transaksi|catatan)\s+([A-Za-z0-9\s]+?)(?:\s+(?:bulan|hari|tanggal|status|sebesar|di|pada)|$)",
            cleaned,
            re.IGNORECASE,
        )
        if m_search:
            kw = m_search.group(1).strip()
            kw_words = [
                w for w in kw.split()
                if w.lower() not in (
                    "ini", "itu", "ada", "semua", "draft", "posted", "cancel",
                    "bulan", "hari", "tanggal", "minggu", "tahun",
                )
            ]
            if kw_words:
                cand = " ".join(kw_words)
                if not is_vendor_name(cand):
                    params["search"] = cand

    date_from, date_to = resolve_relative_dates(cleaned, ref_date)
    if date_from:
        params["date_from"] = date_from
    if date_to:
        params["date_to"] = date_to

    return params


def _extract_single_tool_params(
    plan: Dict[str, Any],
    all_plans: List[Dict[str, Any]],
    user_query: str,
    state: AgentState,
    model: Optional[BaseChatModel],
    today: date,
    retry_count: int,
    retry_instruction: Optional[str],
) -> Dict[str, Any]:
    """Extract and validate parameters for a single planned ERP tool."""
    tool_name = plan.get("tool")
    domain = plan.get("domain") or TOOL_DOMAIN_MAP.get(tool_name, "general")
    sub_query = (plan.get("sub_query") or user_query).strip()

    target_schema = TOOL_SCHEMA_REGISTRY.get(tool_name)
    if target_schema is None:
        if domain == "accounting":
            target_schema = JournalEntriesFilterSchema
        else:
            return {}

    supports_dates = "date_from" in target_schema.model_fields
    rel_date_from, rel_date_to = None, None
    if supports_dates:
        rel_date_from, rel_date_to = resolve_relative_dates(sub_query, today)
        if not rel_date_from and not rel_date_to and sub_query != user_query:
            rel_date_from, rel_date_to = resolve_relative_dates(user_query, today)

    extracted_dict: Optional[Dict[str, Any]] = None

    # 1. LLM Structured Output Attempt
    if model is not None:
        try:
            _, last_day_num = calendar.monthrange(today.year, today.month)
            month_start = today.replace(day=1).strftime("%Y-%m-%d")
            month_end = today.replace(day=last_day_num).strftime("%Y-%m-%d")

            system_prompt = (
                f"You are an expert ERP Parameter Extractor for FastPrint ERP.\n"
                f"Target Tool: {tool_name}\n"
                f"Target Domain: {domain}\n"
                f"Reference Today's Date: {today.strftime('%Y-%m-%d')}\n"
                f"Current Month Range: {month_start} to {month_end}\n\n"
                f"Extract filter entities strictly adhering to the schema.\n"
                f"Guidelines for extracted fields:\n"
                f"- For 'search': Extract ONLY key entity names or document numbers (e.g. 'TikTok', 'PT Sumber Makmur', 'BCA'). Do NOT include full conversational phrases like 'aku mau tanya', 'saldo di jurnal', or relative dates.\n"
                f"- For 'account_code' (in get_account_balance): Only set if user provided an actual account code (e.g. '11285', '111100'). If the user provided a name like 'bank tiktok' or 'marketplace tiktok', do NOT set 'account_code' to the name.\n"
                f"- If the user specifies relative dates like 'bulan ini' or '3 hari terakhir', map them to date_from and date_to.\n"
                f"- If the user refers to entities from previous turns (e.g. 'produk itu', 'barang tadi', 'vendor tersebut', 'itu'), "
                f"resolve the specific SKU, code, or entity name from the conversation history."
            )

            if retry_instruction:
                system_prompt += (
                    f"\n\nIMPORTANT RETRY INSTRUCTION (Attempt {retry_count}):\n"
                    f"{retry_instruction}\n"
                    f"Adjust and relax your extracted filter parameters accordingly."
                )

            system_prompt = build_contextual_prompt(
                system_prompt,
                state.get("conversation_summary"),
            )

            structured_model = model.with_structured_output(target_schema)
            raw_history = (state.get("messages") or [])[:-1]
            history_msgs = trim_conversation_messages(
                raw_history,
                max_messages=settings.MAX_WINDOW_MESSAGES,
            )

            effective_query = user_query if has_anaphoric_reference(sub_query) else sub_query

            messages = [
                SystemMessage(content=system_prompt),
                *history_msgs,
                HumanMessage(content=effective_query),
            ]
            extracted_obj = structured_model.invoke(messages)

            if isinstance(extracted_obj, BaseModel):
                extracted_dict = extracted_obj.model_dump(exclude_none=True)
            elif isinstance(extracted_obj, dict):
                extracted_dict = extracted_obj
            else:
                extracted_dict = {}

            if retry_count == 0 and supports_dates:
                if rel_date_from and not extracted_dict.get("date_from"):
                    extracted_dict["date_from"] = rel_date_from
                if rel_date_to and not extracted_dict.get("date_to"):
                    extracted_dict["date_to"] = rel_date_to
        except Exception:
            extracted_dict = None

    # 2. Deterministic Fallback if LLM unavailable or failed
    if extracted_dict is None:
        if tool_name == "fetch_vendor_invoices":
            extracted_dict = _fallback_extract_vendor_invoice(sub_query, today)
        elif tool_name == "get_stock_level":
            extracted_dict = _fallback_extract_stock_level(sub_query, state.get("messages"))
        elif tool_name == "get_locations":
            extracted_dict = _fallback_extract_locations(sub_query)
        elif tool_name == "get_product_info":
            extracted_dict = _fallback_extract_product_info(sub_query, state.get("messages"))
        elif tool_name == "get_account_balance":
            extracted_dict = _fallback_extract_account_balance(sub_query, today)
        elif tool_name == "get_journal_entries":
            extracted_dict = _fallback_extract_journal_entries(sub_query, today)
        else:
            extracted_dict = {}
            if supports_dates and retry_count == 0:
                if rel_date_from:
                    extracted_dict["date_from"] = rel_date_from
                if rel_date_to:
                    extracted_dict["date_to"] = rel_date_to

    # 3. 3-Tier Entity Resolution & Anaphora Handling
    if tool_name == "get_stock_level":
        if not extracted_dict.get("code") and not extracted_dict.get("product_id"):
            # Tier 1: Peer harvesting (Inventory domain)
            for peer in all_plans:
                if peer is plan:
                    continue
                if peer.get("domain") == "inventory":
                    peer_p = peer.get("params") or {}
                    peer_cand = peer_p.get("code") or peer_p.get("search")
                    if not peer_cand:
                        peer_sub = peer.get("sub_query") or ""
                        peer_ext = _fallback_extract_product_info(peer_sub, state.get("messages"))
                        peer_cand = peer_ext.get("code") or peer_ext.get("search")
                    if peer_cand and not is_vendor_name(peer_cand) and not is_account_code_or_name(peer_cand) and not is_accounting_term(peer_cand):
                        extracted_dict["code"] = peer_cand
                        break
            # Tier 2: Global user_query inspection
            if not extracted_dict.get("code") and sub_query != user_query:
                u_ext = _fallback_extract_product_info(user_query, state.get("messages"))
                cand = u_ext.get("code") or u_ext.get("search")
                if not cand:
                    u_stock = _fallback_extract_stock_level(user_query, state.get("messages"))
                    cand = u_stock.get("code")
                if cand and not is_vendor_name(cand) and not is_account_code_or_name(cand) and not is_accounting_term(cand):
                    extracted_dict["code"] = cand

        if not extracted_dict.get("location_name") and sub_query != user_query:
            u_stock = _fallback_extract_stock_level(user_query, state.get("messages"))
            if u_stock.get("location_name"):
                extracted_dict["location_name"] = u_stock["location_name"]

    elif tool_name == "get_product_info":
        if not extracted_dict.get("code") and not extracted_dict.get("search") and not extracted_dict.get("product_id"):
            # Tier 1: Peer harvesting (Inventory domain)
            for peer in all_plans:
                if peer is plan:
                    continue
                if peer.get("domain") == "inventory":
                    peer_p = peer.get("params") or {}
                    peer_cand = peer_p.get("code") or peer_p.get("search")
                    if not peer_cand:
                        peer_sub = peer.get("sub_query") or ""
                        peer_ext = _fallback_extract_stock_level(peer_sub, state.get("messages"))
                        peer_cand = peer_ext.get("code")
                    if peer_cand and not is_vendor_name(peer_cand) and not is_account_code_or_name(peer_cand) and not is_accounting_term(peer_cand):
                        extracted_dict["search"] = peer_cand
                        break
            # Tier 2: Global user_query inspection
            if not extracted_dict.get("code") and not extracted_dict.get("search") and sub_query != user_query:
                u_ext = _fallback_extract_product_info(user_query, state.get("messages"))
                if u_ext.get("code") and not is_vendor_name(u_ext["code"]) and not is_account_code_or_name(u_ext["code"]) and not is_accounting_term(u_ext["code"]):
                    extracted_dict["code"] = u_ext["code"]
                elif u_ext.get("search") and not is_vendor_name(u_ext["search"]) and not is_account_code_or_name(u_ext["search"]) and not is_accounting_term(u_ext["search"]):
                    extracted_dict["search"] = u_ext["search"]

    elif tool_name == "fetch_vendor_invoices":
        if not extracted_dict.get("vendor_name") and not extracted_dict.get("vendor_id"):
            # Tier 1: Peer harvesting (Accounting domain)
            for peer in all_plans:
                if peer is plan:
                    continue
                if peer.get("domain") == "accounting":
                    peer_p = peer.get("params") or {}
                    peer_vendor = peer_p.get("vendor_name")
                    if peer_vendor and is_vendor_name(peer_vendor):
                        extracted_dict["vendor_name"] = peer_vendor
                        break
            # Tier 2: Global user_query inspection
            if not extracted_dict.get("vendor_name") and sub_query != user_query:
                v_ext = _fallback_extract_vendor_invoice(user_query, today)
                if v_ext.get("vendor_name") and is_vendor_name(v_ext["vendor_name"]):
                    extracted_dict["vendor_name"] = v_ext["vendor_name"]
                if not extracted_dict.get("status") and v_ext.get("status"):
                    extracted_dict["status"] = v_ext["status"]

    elif tool_name == "get_account_balance":
        if not extracted_dict.get("account_code") and not extracted_dict.get("account_id") and sub_query != user_query:
            b_ext = _fallback_extract_account_balance(user_query, today)
            if b_ext.get("account_code"):
                extracted_dict["account_code"] = b_ext["account_code"]

    elif tool_name == "get_journal_entries":
        if not extracted_dict.get("entry_number") and not extracted_dict.get("status") and not extracted_dict.get("search") and sub_query != user_query:
            j_ext = _fallback_extract_journal_entries(user_query, today)
            for k in ("entry_number", "status", "search"):
                if j_ext.get(k):
                    extracted_dict[k] = j_ext[k]

    elif tool_name == "get_locations":
        if not extracted_dict.get("search") and sub_query != user_query:
            l_ext = _fallback_extract_locations(user_query)
            if l_ext.get("search"):
                extracted_dict["search"] = l_ext["search"]

    # 4. Global Temporal Date Backfill (Accounting Domain Only)
    if supports_dates and retry_count == 0:
        if not extracted_dict.get("date_from") and rel_date_from:
            extracted_dict["date_from"] = rel_date_from
        if not extracted_dict.get("date_to") and rel_date_to:
            extracted_dict["date_to"] = rel_date_to

    # 5. Staged Retry Relaxation
    if retry_count == 1:
        extracted_dict.pop("date_from", None)
        extracted_dict.pop("date_to", None)
    elif retry_count >= 2:
        extracted_dict.pop("date_from", None)
        extracted_dict.pop("date_to", None)
        if tool_name == "fetch_vendor_invoices":
            extracted_dict.pop("vendor_name", None)
            extracted_dict.pop("search", None)
        elif tool_name == "get_journal_entries":
            extracted_dict.pop("status", None)
            extracted_dict.pop("search", None)

    # 6. Schema Filtering / Validation
    try:
        validated_obj = target_schema(**extracted_dict)
        clean_params = validated_obj.model_dump(exclude_none=True)
    except Exception:
        valid_keys = set(target_schema.model_fields.keys())
        clean_params = {k: v for k, v in extracted_dict.items() if k in valid_keys and v is not None}

    return clean_params


def extractor_node(
    state: AgentState,
    llm: Optional[BaseChatModel] = None,
    ref_date: Optional[date] = None,
) -> Dict[str, Any]:
    """
    LangGraph pipeline node that extracts structured tool filter parameters from user query.
    Iterates across active_tools subagent plans with 3-tier entity resolution and fallback heuristics,
    while maintaining backward compatibility with legacy scalar detected_tool and extracted_params.

    Args:
        state: Current AgentState.
        llm: Optional BaseChatModel instance with structured output capabilities.
        ref_date: Reference anchor date for relative date resolution (defaults to date.today()).

    Returns:
        Partial AgentState update dictionary with:
            - active_tools: Updated subagent plans with validated params dictionaries.
            - extracted_params: Dictionary of parsed and validated parameter filters for primary tool.
    """
    user_query = state.get("user_query")
    if not user_query and state.get("messages"):
        last_msg = state["messages"][-1]
        user_query = getattr(last_msg, "content", "") or ""

    user_query = (user_query or "").strip()
    from app.graph.nodes.router import find_previous_user_query, is_retry_query
    if is_retry_query(user_query):
        prior_q = find_previous_user_query(state.get("messages"))
        if prior_q:
            user_query = prior_q

    raw_active_tools = state.get("active_tools")
    detected_tool = state.get("detected_tool")
    detected_domain = state.get("detected_domain")
    retry_count = int(state.get("retry_count") or 0)
    retry_instruction = state.get("retry_instruction")
    today = ref_date or date.today()

    # Guard: If neither active_tools nor detected_tool was provided, return empty extracted params
    if not raw_active_tools and not detected_tool:
        return {"extracted_params": {}}

    # Normalize active_tools list with deep copies to maintain strict state immutability
    working_active_tools: List[Dict[str, Any]] = []
    if raw_active_tools:
        working_active_tools = [copy.deepcopy(plan) for plan in raw_active_tools]
    elif detected_tool:
        domain = detected_domain or TOOL_DOMAIN_MAP.get(detected_tool, "general")
        working_active_tools = [{
            "tool": detected_tool,
            "domain": domain,
            "sub_query": user_query,
            "params": {},
            "status": "pending",
        }]

    if not working_active_tools:
        return {"extracted_params": {}}

    # Initialize model if not in test env
    model = llm
    is_test_env = "unittest" in sys.modules or os.environ.get("FASTPRINT_TEST_ENV") == "true"
    if model is None and not is_test_env:
        try:
            model = get_chat_model()
        except Exception:
            model = None

    # Multi-tool extraction iteration
    for plan in working_active_tools:
        extracted = _extract_single_tool_params(
            plan=plan,
            all_plans=working_active_tools,
            user_query=user_query,
            state=state,
            model=model,
            today=today,
            retry_count=retry_count,
            retry_instruction=retry_instruction,
        )
        plan["params"] = extracted

    primary_params = working_active_tools[0].get("params", {}) if working_active_tools else {}

    return {
        "active_tools": working_active_tools,
        "extracted_params": primary_params,
    }
