"""
Synthesizer Node for FastPrint ERP LangGraph Agent Workflow (TASK-4.5).
Generates structured, professional final answers formatted in Indonesian Markdown
based on state verification status, tool execution data, calculations, and error messages.
"""
import logging
from typing import Any, Dict, List, Optional
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.graph.state import AgentState
from app.services.llm import get_chat_model

logger = logging.getLogger("synthesizer_node")


def _format_rupiah(value: Any) -> str:
    """Format numeric values into standard Indonesian Rupiah notation."""
    try:
        num = float(value)
        return f"Rp {num:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except (ValueError, TypeError):
        return str(value)


def format_deterministic_answer(state: AgentState) -> str:
    """
    Deterministic template formatter for ERP results when offline or LLM is unavailable.
    Guarantees consistent, professional markdown output.
    """
    status = state.get("verification_status")
    domain = state.get("detected_domain")
    tool = state.get("detected_tool")
    tool_result = state.get("tool_result") or {}
    user_query = state.get("user_query") or ""
    error_message = state.get("error_message")

    # 1. Error Status Handling
    if status == "ERROR":
        msg = error_message or "Terjadi kesalahan internal pada layanan ERP."
        return (
            f"### Kendala Akses Data ERP\n\n"
            f"Permintaan Anda tidak dapat diproses karena terjadi kendala teknis:\n"
            f"> **Pesan Kesalahan**: {msg}\n\n"
            f"**Saran Tindakan**:\n"
            f"- Pastikan token autentikasi (JWT) masih aktif atau lakukan login ulang.\n"
            f"- Pastikan pengguna Anda memiliki hak akses ke modul dan perusahaan (Company ID) yang diminta."
        )

    # 2. General Domain / Guardrail Inquiries
    if domain == "general" or not tool:
        query_lower = user_query.lower()
        if any(term in query_lower for term in ("pelanggan", "customer", "piutang")):
            return (
                "### Informasi Tagihan Pelanggan (Customer)\n\n"
                "Mohon maaf, sistem AI Orchestrator saat ini dikhususkan untuk **tagihan vendor / pemasok**, "
                "buku jurnal, saldo akun kas, dan inventaris barang.\n\n"
                "Untuk memeriksa tagihan atau piutang pelanggan, silakan akses modul **Penjualan (Sales / Invoicing)** "
                "secara langsung di aplikasi FastPrint ERP."
            )
        return (
            "Halo! Saya adalah Asisten AI FastPrint ERP. Saya dapat membantu Anda memeriksa:\n"
            "- **Tagihan Vendor / Pemasok** (Hutang usaha dan status pembayaran)\n"
            "- **Saldo Akun Buku Kas & Neraca** (Accounting Balance)\n"
            "- **Entri Jurnal Umum** (General Journal Entries)\n"
            "- **Stok Barang & Master Produk** (Inventory & Warehouse Stock)\n\n"
            "Silakan sampaikan pertanyaan spesifik Anda!"
        )

    # 3. Empty Status (No Data Found after Max Retries)
    if status == "EMPTY":
        params = state.get("extracted_params") or {}
        filter_hints = []
        if params.get("vendor_name"):
            filter_hints.append(f"Vendor: **{params['vendor_name']}**")
        if params.get("date_from") or params.get("date_to"):
            filter_hints.append(f"Periode: **{params.get('date_from', '-')} s/d {params.get('date_to', '-')}**")
        if params.get("account_code"):
            filter_hints.append(f"Kode Akun: **{params['account_code']}**")

        hint_str = f" ({', '.join(filter_hints)})" if filter_hints else ""
        return (
            f"### Data Tidak Ditemukan\n\n"
            f"Setelah dilakukan penelusuran pada database ERP FastPrint{hint_str}, "
            f"tidak ditemukan data transaksi yang sesuai.\n\n"
            f"**Catatan Verifikasi**:\n"
            f"- Sistem telah mencoba relaksasi batasan filter tanggal dan kata kunci pencarian secara otomatis (maksimal 2x).\n"
            f"- Silakan periksa kembali ketepatan nomor dokumen, nama vendor, atau pastikan periode transaksi telah tercatat di ERP."
        )

    # 4. Valid Data Presentation
    lines: List[str] = []

    # 4.1 Vendor Invoices / Journal Entries
    if tool in ("fetch_vendor_invoices", "get_journal_entries"):
        entries = tool_result.get("entries") or []
        total_count = tool_result.get("total_count", len(entries))
        calculations = tool_result.get("calculations") or {}

        lines.append(f"### Laporan Transaksi Tagihan / Jurnal ({total_count} Catatan Ditemukan)\n")
        if entries:
            lines.append("| No | Nomor Dokumen | Tanggal | Rekanan / Vendor | Total Debit | Status |")
            lines.append("|:---|:---|:---|:---|:---|:---|")
            for idx, item in enumerate(entries, start=1):
                num = item.get("number") or item.get("entry_number") or "-"
                tgl = item.get("date") or "-"
                vendor = item.get("vendor_name") or item.get("journal_name") or "-"
                debit = _format_rupiah(item.get("total_debit", 0.0))
                st = item.get("status") or item.get("state") or "posted"
                lines.append(f"| {idx} | `{num}` | {tgl} | {vendor} | {debit} | {st.upper()} |")

        # Summary calculations block (DoD TASK-4.3 preservation)
        if calculations:
            lines.append("\n#### Ringkasan Kalkulasi Total:")
            if "total_unpaid" in calculations:
                lines.append(f"- **Total Belum Dibayar**: {_format_rupiah(calculations['total_unpaid'])}")
            if "total_paid" in calculations:
                lines.append(f"- **Total Sudah Dibayar**: {_format_rupiah(calculations['total_paid'])}")
            if "grand_total" in calculations:
                lines.append(f"- **Grand Total Tagihan**: {_format_rupiah(calculations['grand_total'])}")
        return "\n".join(lines)

    # 4.2 Account Balance
    if tool == "get_account_balance":
        accounts = tool_result.get("accounts") or []
        lines.append("### Informasi Saldo Chart of Account (Accounting Balance)\n")
        if accounts:
            lines.append("| Kode Akun | Nama Akun | Total Debit | Total Kredit | Saldo Bersih | Posisi |")
            lines.append("|:---|:---|:---|:---|:---|:---|")
            for acc in accounts:
                code = acc.get("account_code") or "-"
                name = acc.get("account_name") or "-"
                deb = _format_rupiah(acc.get("total_debit", 0.0))
                kred = _format_rupiah(acc.get("total_credit", 0.0))
                net = _format_rupiah(acc.get("net_balance", 0.0))
                pos = acc.get("balance_position") or "DEBIT"
                lines.append(f"| `{code}` | {name} | {deb} | {kred} | **{net}** | {pos} |")
        else:
            net = _format_rupiah(tool_result.get("net_balance", 0.0))
            deb = _format_rupiah(tool_result.get("total_debit", 0.0))
            kred = _format_rupiah(tool_result.get("total_credit", 0.0))
            lines.append(f"- **Total Debit**: {deb}")
            lines.append(f"- **Total Kredit**: {kred}")
            lines.append(f"- **Saldo Bersih**: **{net}**")
        return "\n".join(lines)

    # 4.3 Stock Level
    if tool == "get_stock_level":
        lines.append("### Informasi Ketersediaan Stok Barang (Inventory)\n")
        if isinstance(tool_result, list):
            items = tool_result
        else:
            items = [tool_result]

        lines.append("| SKU / Kode | Nama Produk | Perusahaan | Qty Fisik |")
        lines.append("|:---|:---|:---|:---|")
        for st_item in items:
            code = st_item.get("code") or st_item.get("sku") or "-"
            name = st_item.get("name") or st_item.get("product_name") or "-"
            comp = st_item.get("company_name") or f"Company #{st_item.get('company_id', 1)}"
            qty = st_item.get("qty_on_hand") or st_item.get("quantity") or 0.0
            lines.append(f"| `{code}` | {name} | {comp} | **{qty} unit** |")
        return "\n".join(lines)

    # 4.4 Product Info
    if tool == "get_product_info":
        lines.append("### Informasi Master Produk (Catalog)\n")
        products = tool_result.get("products") if isinstance(tool_result, dict) else (
            tool_result if isinstance(tool_result, list) else [tool_result]
        )
        if products:
            for p in products:
                lines.append(f"- **Nama Produk**: {p.get('name', '-')}")
                lines.append(f"  - Kode / SKU: `{p.get('code') or p.get('sku', '-')}`")
                if "price" in p or "sales_price" in p:
                    lines.append(f"  - Harga Jual: {_format_rupiah(p.get('price') or p.get('sales_price', 0.0))}")
        return "\n".join(lines)

    # Default fallback
    return f"Hasil eksekusi data ERP: {str(tool_result)}"


def synthesizer_node(
    state: AgentState,
    llm: Optional[BaseChatModel] = None,
) -> Dict[str, Any]:
    """
    LangGraph pipeline node that synthesizes the final conversational response.

    Args:
        state: Current AgentState.
        llm: Optional BaseChatModel instance for natural language synthesis.

    Returns:
        Partial AgentState update dictionary containing:
            - final_answer: Markdown formatted response string
            - messages: [AIMessage(content=final_answer)]
    """
    user_query = state.get("user_query")
    status = state.get("verification_status")
    deterministic_answer = format_deterministic_answer(state)

    final_text = deterministic_answer

    # Attempt LLM natural language polish if LLM is provided/available
    model = llm
    if model is None:
        try:
            model = get_chat_model()
        except Exception:
            model = None

    if model is not None and status == "VALID":
        try:
            prompt = (
                "Anda adalah Asisten AI FastPrint ERP yang profesional dan teliti.\n"
                "Sajikan data laporan berikut dalam Bahasa Indonesia yang formal, ringkas, dan jelas.\n"
                "Pertahankan seluruh angka transaksi, kode, dan rincian tabel Markdown dengan akurat tanpa mengubah nilai.\n\n"
                f"Pertanyaan Pengguna: {user_query}\n"
                f"Data Laporan Terstruktur:\n{deterministic_answer}"
            )
            response = model.invoke([
                SystemMessage(content="Anda adalah Asisten AI FastPrint ERP."),
                HumanMessage(content=prompt),
            ])
            content = getattr(response, "content", None)
            if content and isinstance(content, str) and content.strip():
                final_text = content.strip()
        except Exception as exc:
            logger.warning(f"LLM synthesis failed, falling back to deterministic output: {exc}")
            final_text = deterministic_answer

    return {
        "final_answer": final_text,
        "messages": [AIMessage(content=final_text)],
    }
