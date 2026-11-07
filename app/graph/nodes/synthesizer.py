"""
Synthesizer Node for FastPrint ERP LangGraph Agent Workflow (TASK-4.5).
Generates structured, professional final answers formatted in Indonesian Markdown
based on state verification status, tool execution data, calculations, and error messages.
"""
import logging
import os
import sys
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


def _format_rag_section(
    rag_context: Optional[List[Dict[str, Any]]],
    rag_sources: Optional[List[str]] = None,
) -> str:
    """Format retrieved RAG knowledge chunks and sources into structured Markdown."""
    if not rag_context:
        return ""
    lines = ["### Rujukan Pengetahuan & Dokumen SOP\n"]
    for i, chunk in enumerate(rag_context, 1):
        title = chunk.get("title") or chunk.get("metadata", {}).get("source") or f"Dokumen {i}"
        content = chunk.get("content", "").strip()
        score = chunk.get("similarity_score")
        score_str = f" (Relevansi: {score:.0%})" if score is not None else ""
        lines.append(f"#### {i}. {title}{score_str}")
        lines.append(f"{content}\n")

    sources = rag_sources or [c.get("title") for c in rag_context if c.get("title")]
    if sources:
        clean_sources = list(dict.fromkeys(sources))
        lines.append("**Sumber Rujukan Terverifikasi**:")
        for s in clean_sources:
            lines.append(f"- `{s}`")
    return "\n".join(lines)


def _format_single_tool_presentation(tool: Optional[str], tool_result: Any) -> str:
    """Format an individual tool's response payload into Markdown."""
    if not tool or not tool_result:
        return ""

    lines: List[str] = []

    # 1. Vendor Invoices / Journal Entries
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

    # 2. Account Balance
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

    # 3. Stock Level
    if tool == "get_stock_level":
        lines.append("### Informasi Ketersediaan Stok Barang (Inventory)\n")
        if isinstance(tool_result, list):
            items = tool_result
        else:
            items = [tool_result]

        lines.append("| SKU / Kode | Nama Produk | Perusahaan | Lokasi / Gudang | Qty Fisik |")
        lines.append("|:---|:---|:---|:---|:---|")
        for st_item in items:
            code = st_item.get("code") or st_item.get("sku") or "-"
            name = st_item.get("name") or st_item.get("product_name") or "-"
            comp = st_item.get("company_name") or f"Company #{st_item.get('company_id', 1)}"
            loc = st_item.get("location_name") or (f"Location #{st_item.get('location_id')}" if st_item.get("location_id") else "Semua Lokasi")
            qty = st_item.get("qty_on_hand") or st_item.get("quantity") or 0.0
            lines.append(f"| `{code}` | {name} | {comp} | {loc} | **{qty} unit** |")
        return "\n".join(lines)

    # 4. Product Info
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

    # 5. Locations
    if tool == "get_locations":
        locs = tool_result.get("locations") if isinstance(tool_result, dict) else (
            tool_result if isinstance(tool_result, list) else [tool_result]
        )
        total_found = tool_result.get("total_found", len(locs)) if isinstance(tool_result, dict) else len(locs)
        lines.append(f"### Informasi Daftar Lokasi & Gudang ({total_found} Lokasi Ditemukan)\n")
        if locs:
            lines.append("| ID | Nama Lokasi | Gudang Induk | Tipe | Kategori |")
            lines.append("|:---|:---|:---|:---|:---|")
            for l in locs:
                lid = l.get("id", "-")
                lname = l.get("name", "-")
                wh = l.get("warehouse_name") or "-"
                ltype = (l.get("type") or "-").capitalize()
                lcat = (l.get("category") or "-").capitalize()
                lines.append(f"| `{lid}` | {lname} | {wh} | {ltype} | {lcat} |")
        return "\n".join(lines)

    # Default fallback
    return f"Hasil eksekusi data ERP: {str(tool_result)}"


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
    multi_results = state.get("multi_tool_results") or {}
    is_multi = state.get("is_multi_tool") or (len(multi_results) > 1)

    # 0. Empty History Retry Handling
    if error_message == "NO_PREVIOUS_QUERY_TO_RETRY":
        return (
            "### Tidak Ada Permintaan Sebelumnya\n\n"
            "Belum ada riwayat transaksi atau pertanyaan sebelumnya pada sesi percakapan ini untuk diulangi.\n\n"
            "Silakan sampaikan pertanyaan spesifik Anda seputar data FastPrint ERP:\n"
            "- **Tagihan Vendor / Pemasok** (Hutang usaha & status verifikasi faktur)\n"
            "- **Saldo Akun Buku Kas & Neraca** (Accounting Balance & Kas Kecil)\n"
            "- **Entri Jurnal Umum** (General Journal Entries)\n"
            "- **Stok Barang & Master Produk** (Inventory & Kuantitas Gudang)\n"
            "- **Pengetahuan & SOP Perusahaan** (SOP Retur, Kebijakan, dan Spesifikasi Teknis)"
        )

    # 1. Error Status Handling
    if status == "ERROR":
        msg = error_message or "Terjadi kesalahan internal pada layanan ERP."
        if "PRODUCT_NOT_FOUND" in msg or "Product variant not found" in msg:
            params = state.get("extracted_params") or {}
            identifier = params.get("code") or params.get("product_id") or "yang diminta"
            return (
                f"### Produk Tidak Ditemukan\n\n"
                f"Produk **{identifier}** tidak ditemukan pada master data ERP FastPrint.\n\n"
                f"**Saran Tindakan**:\n"
                f"- Pastikan kode SKU, barcode, atau nama produk yang dimasukkan sudah sesuai.\n"
                f"- Periksa apakah produk tersebut aktif dan tidak dalam status diarsipkan (archive) di ERP."
            )

        return (
            f"### Kendala Akses Data ERP\n\n"
            f"Permintaan Anda tidak dapat diproses karena terjadi kendala teknis:\n"
            f"> **Pesan Kesalahan**: {msg}\n\n"
            f"**Saran Tindakan**:\n"
            f"- Pastikan token autentikasi (JWT) masih aktif atau lakukan login ulang.\n"
            f"- Pastikan pengguna Anda memiliki hak akses ke modul dan perusahaan (Company ID) yang diminta."
        )

    # 2. Out of Scope Guardrail
    if domain == "out_of_scope":
        return (
            "### Permintaan di Luar Cakupan (Out of Scope)\n\n"
            "Mohon maaf, permintaan Anda tidak dapat diproses karena berada di luar cakupan operasional Asisten AI FastPrint ERP.\n\n"
            "Sistem ini dikhususkan untuk melayani penelusuran data operasional dan pembukuan internal perusahaan:\n"
            "- **Tagihan Vendor / Pemasok** (Hutang usaha & status verifikasi faktur)\n"
            "- **Saldo Akun Buku Kas & Neraca** (Accounting Balance & Kas Kecil)\n"
            "- **Entri Jurnal Umum** (General Journal Entries)\n"
            "- **Stok Barang & Master Produk** (Inventory & Kuantitas Gudang)\n"
            "- **Pengetahuan & SOP Perusahaan** (SOP Retur, Kebijakan, dan Spesifikasi Teknis)\n\n"
            "Silakan sampaikan pertanyaan spesifik Anda seputar data dan modul operasional di atas."
        )

    query_mode = state.get("query_mode")
    rag_context = state.get("rag_context")
    rag_sources = state.get("rag_sources")

    # 2.1 Pure RAG Knowledge Handling
    if domain == "knowledge" or query_mode == "vector_rag":
        if status == "EMPTY" or not rag_context:
            return (
                "### Informasi Pengetahuan Tidak Ditemukan\n\n"
                "Dokumen SOP, kebijakan, atau spesifikasi terkait pertanyaan Anda belum terdaftar "
                "dalam basis data pengetahuan FastPrint ERP.\n\n"
                "**Saran Tindakan**:\n"
                "- Pastikan kata kunci menggunakan istilah baku operasional (misal: 'SOP Retur', 'Spesifikasi Art Paper').\n"
                "- Hubungi administrator sistem untuk mendaftarkan dokumen SOP ini ke basis data pengetahuan."
            )
        return _format_rag_section(rag_context, rag_sources)

    # 3. General Domain / Guardrail Inquiries
    if (domain == "general" or not tool) and not is_multi:
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
        if params.get("code"):
            filter_hints.append(f"Produk / SKU: **{params['code']}**")
        elif params.get("product_id"):
            filter_hints.append(f"Product ID: **{params['product_id']}**")

        hint_str = f" ({', '.join(filter_hints)})" if filter_hints else ""
        return (
            f"### Data Tidak Ditemukan\n\n"
            f"Setelah dilakukan penelusuran pada database ERP FastPrint{hint_str}, "
            f"tidak ditemukan data transaksi yang sesuai.\n\n"
            f"**Catatan Verifikasi**:\n"
            f"- Sistem telah mencoba relaksasi batasan filter tanggal dan kata kunci pencarian secara otomatis (maksimal 2x).\n"
            f"- Silakan periksa kembali ketepatan nomor dokumen, nama vendor, atau pastikan periode transaksi telah tercatat di ERP."
        )

    multi_results = state.get("multi_tool_results") or {}
    is_multi = state.get("is_multi_tool") or (len(multi_results) > 1)

    # 4. Multi-Tool Execution Unified Formatting (Milestone 4)
    if is_multi and multi_results:
        sections: List[str] = []
        active_tools = state.get("active_tools") or []
        ordered_tools = [p.get("tool") for p in active_tools if p.get("tool")]
        if not ordered_tools:
            ordered_tools = list(multi_results.keys())

        for t_name in ordered_tools:
            res = multi_results.get(t_name)
            if res:
                formatted = _format_single_tool_presentation(t_name, res)
                if formatted:
                    sections.append(formatted)
            else:
                domain_label = "Modul ERP"
                if "stock" in t_name or "product" in t_name or "location" in t_name:
                    domain_label = "Inventaris / Stok"
                elif "balance" in t_name or "invoice" in t_name or "journal" in t_name:
                    domain_label = "Akuntansi / Keuangan"
                sections.append(
                    f"### Informasi {domain_label} (`{t_name}`)\n\n"
                    f"Tidak ditemukan data atau terjadi kendala saat memuat informasi untuk modul ini."
                )

        if sections:
            return "\n\n---\n\n".join(sections)

    # 5. Single-Tool Valid Data Presentation (Backward Compatibility)
    return _format_single_tool_presentation(tool, tool_result)


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
    query_mode = state.get("query_mode")
    retry_instruction = state.get("retry_instruction") or ""
    retry_header = (
        f"> 🔄 **{retry_instruction}**\n\n"
        if retry_instruction.startswith("Mengulang permintaan:")
        else ""
    )
    deterministic_answer = format_deterministic_answer(state)

    # For hybrid inquiries, combine live ERP results with semantic RAG findings
    if query_mode == "hybrid":
        rag_context = state.get("rag_context")
        rag_sources = state.get("rag_sources")
        rag_section = _format_rag_section(rag_context, rag_sources)
        if rag_section:
            deterministic_answer = f"{deterministic_answer}\n\n---\n\n{rag_section}"
        else:
            deterministic_answer = (
                f"{deterministic_answer}\n\n"
                f"> [!NOTE]\n"
                f"> Dokumen SOP atau spesifikasi kebijakan terkait belum terdaftar di basis data pengetahuan perusahaan."
            )

    final_text = deterministic_answer

    # Attempt LLM natural language polish if LLM is provided/available
    model = llm
    is_test_env = "unittest" in sys.modules or os.environ.get("FASTPRINT_TEST_ENV") == "true"
    if model is None and not is_test_env:
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

    if retry_header and not final_text.startswith("> 🔄"):
        final_text = f"{retry_header}{final_text}"

    return {
        "final_answer": final_text,
        "messages": [AIMessage(content=final_text)],
    }
