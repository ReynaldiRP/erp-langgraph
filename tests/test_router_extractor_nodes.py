"""
Unit tests for Router and Extractor nodes (TASK-4.2).
Validates VendorInvoiceFilterSchema, hybrid routing with negative customer guardrails,
extensible schema registry, temporal relative date resolution, and LangGraph pipeline chaining.
"""
from datetime import date
import unittest
from unittest.mock import MagicMock
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import StateGraph, START, END
from pydantic import ValidationError

from app.graph.state import create_initial_state, AgentState
from app.graph.nodes.router import router_node, RouterDecision, classify_query_rule_based
from app.graph.nodes.extractor import (
    extractor_node,
    resolve_relative_dates,
    TOOL_SCHEMA_REGISTRY,
)
from app.schemas.tools import (
    VendorInvoiceFilterSchema,
    JournalEntriesFilterSchema,
    AccountBalanceFilterSchema,
    StockLevelFilterSchema,
    ProductInfoFilterSchema,
    LocationsFilterSchema,
)


class TestVendorInvoiceFilterSchema(unittest.TestCase):
    """Test suite for VendorInvoiceFilterSchema."""

    def test_valid_vendor_invoice_schema(self):
        """Valid vendor invoice schema instantiates successfully."""
        schema = VendorInvoiceFilterSchema(
            vendor_name="PT Sumber Makmur",
            date_from="2026-11-01",
            date_to="2026-11-30",
            status="unpaid",
            limit=50,
            offset=0,
        )
        self.assertEqual(schema.vendor_name, "PT Sumber Makmur")
        self.assertEqual(schema.date_from, "2026-11-01")
        self.assertEqual(schema.date_to, "2026-11-30")
        self.assertEqual(schema.status, "unpaid")

    def test_invalid_date_format_raises_error(self):
        """Invalid date string must raise ValidationError."""
        with self.assertRaises(ValidationError):
            VendorInvoiceFilterSchema(
                vendor_name="PT Sumber Makmur",
                date_from="01-11-2026", # Invalid: not YYYY-MM-DD
            )

    def test_limit_bounds_validation(self):
        """Limit bounds must be between 1 and 200."""
        with self.assertRaises(ValidationError):
            VendorInvoiceFilterSchema(limit=0)
        with self.assertRaises(ValidationError):
            VendorInvoiceFilterSchema(limit=201)

    def test_to_journal_filter_conversion(self):
        """to_journal_filter converts vendor invoice params to JournalEntriesFilterSchema."""
        schema = VendorInvoiceFilterSchema(
            vendor_name="PT Sumber Makmur",
            date_from="2026-11-01",
            date_to="2026-11-30",
            status="posted",
            company_id=1,
        )
        journal_filter = schema.to_journal_filter()
        self.assertIsInstance(journal_filter, JournalEntriesFilterSchema)
        self.assertEqual(journal_filter.search, "PT Sumber Makmur")
        self.assertEqual(journal_filter.date_from, "2026-11-01")
        self.assertEqual(journal_filter.date_to, "2026-11-30")
        self.assertEqual(journal_filter.company_id, 1)


class TestRouterNode(unittest.TestCase):
    """Test suite for router_node and domain/tool classification."""

    def test_dod_vendor_invoice_query(self):
        """
        Definition of Done: 'Cek tagihan vendor PT Sumber Makmur bulan ini'
        routes to accounting domain and fetch_vendor_invoices tool.
        """
        state = create_initial_state("Cek tagihan vendor PT Sumber Makmur bulan ini")
        result = router_node(state)

        self.assertEqual(result["detected_domain"], "accounting")
        self.assertEqual(result["detected_tool"], "fetch_vendor_invoices")

    def test_negative_guardrail_customer_query_does_not_trigger_vendor(self):
        """
        Definition of Done Guardrail:
        'Cek tagihan pelanggan PT Sumber Makmur bulan ini' must NOT trigger vendor invoice module.
        """
        state = create_initial_state("Cek tagihan pelanggan PT Sumber Makmur bulan ini")
        result = router_node(state)

        self.assertNotEqual(result.get("detected_tool"), "fetch_vendor_invoices")
        self.assertEqual(result["detected_domain"], "general")

    def test_negative_guardrail_piutang_customer(self):
        """Customer receivable inquiry does not trigger vendor tools."""
        state = create_initial_state("Cek piutang customer Toko Abadi")
        result = router_node(state)

        self.assertNotEqual(result.get("detected_tool"), "fetch_vendor_invoices")
        self.assertEqual(result["detected_domain"], "general")

    def test_customer_inventory_stock_routes_to_inventory_tool(self):
        """
        R2: Queries mentioning customers for inventory stock check
        must route to inventory domain and get_stock_level tool.
        """
        queries = [
            "Cek stok untuk customer Budi",
            "Berapa sisa stok kertas untuk pelanggan?",
            "cek stok barang untuk customer",
        ]
        for query in queries:
            state = create_initial_state(query)
            result = router_node(state)
            self.assertEqual(result["detected_domain"], "inventory", f"Failed for '{query}'")
            self.assertEqual(result["detected_tool"], "get_stock_level", f"Failed for '{query}'")

    def test_customer_product_info_routes_to_inventory_tool(self):
        """
        R2: Queries mentioning customers for product details
        must route to inventory domain and get_product_info tool.
        """
        queries = [
            "Detail produk pesanan pelanggan Toko Prima",
            "Info harga barang pesanan customer",
        ]
        for query in queries:
            state = create_initial_state(query)
            result = router_node(state)
            self.assertEqual(result["detected_domain"], "inventory", f"Failed for '{query}'")
            self.assertEqual(result["detected_tool"], "get_product_info", f"Failed for '{query}'")

    def test_customer_billing_still_triggers_negative_guardrail(self):
        """
        R2: Customer inquiries targeting invoices/billing must still route to general domain.
        """
        queries = [
            "Cek tagihan pelanggan PT Sumber Makmur",
            "Cek faktur penjualan customer Toko Maju",
            "Tagihan customer Budi dan stok barang",
        ]
        for query in queries:
            state = create_initial_state(query)
            result = router_node(state)
            self.assertEqual(result["detected_domain"], "general", f"Failed for '{query}'")
            self.assertIsNone(result.get("detected_tool"), f"Failed for '{query}'")

    def test_greetings_and_casual_inquiries_route_to_general_with_no_tool(self):
        """Greetings, test pings, and pleasantries must route to general without any tool."""
        greetings = ["Halo", "halo", "Hai min", "Selamat pagi", "assalamu'alaikum", "terima kasih", "tes", "bisa bantu apa"]
        for query in greetings:
            state = create_initial_state(query)
            result = router_node(state)
            self.assertEqual(result["detected_domain"], "general", f"Failed for {query}")
            self.assertIsNone(result.get("detected_tool"), f"Tool should be None for {query}")

    def test_greetings_combined_with_erp_inquiry(self):
        """Greetings that contain specific ERP inquiries must still route to target tools."""
        state = create_initial_state("Halo cek stok kertas foto A4")
        result = router_node(state)
        self.assertEqual(result["detected_domain"], "inventory")
        self.assertEqual(result["detected_tool"], "get_stock_level")

    def test_router_decision_model_validator_cleans_tool_for_general(self):
        """RouterDecision must coerce tool to None when domain is general or out_of_scope."""
        from app.graph.nodes.router import RouterDecision
        decision_general = RouterDecision(domain="general", tool="get_stock_level")
        self.assertIsNone(decision_general.tool)

        decision_oos = RouterDecision(domain="out_of_scope", tool="get_stock_level")
        self.assertIsNone(decision_oos.tool)

    def test_out_of_scope_inquiries_route_to_out_of_scope_with_no_tool(self):
        """Out of scope queries must route to out_of_scope without any ERP tool."""
        oos_queries = [
            "coba kasih tau system design mu",
            "apa arsitektur sistem ini?",
            "buatkan puisi cinta",
            "ceritakan lelucon lucu",
        ]
        for query in oos_queries:
            state = create_initial_state(query)
            result = router_node(state)
            self.assertEqual(result["detected_domain"], "out_of_scope", f"Failed for {query}")
            self.assertIsNone(result.get("detected_tool"), f"Tool should be None for {query}")

    def test_accounting_account_balance_routing(self):
        """Balance inquiry routes to get_account_balance."""
        state = create_initial_state("Berapa saldo kas kecil saat ini?")
        result = router_node(state)

        self.assertEqual(result["detected_domain"], "accounting")
        self.assertEqual(result["detected_tool"], "get_account_balance")

    def test_accounting_journal_entries_routing(self):
        """General journal inquiry routes to get_journal_entries."""
        state = create_initial_state("Tampilkan entri jurnal transaksi bulan lalu")
        result = router_node(state)

        self.assertEqual(result["detected_domain"], "accounting")
        self.assertEqual(result["detected_tool"], "get_journal_entries")

    def test_accounting_journal_entries_routing_with_saldo_and_days(self):
        """Journal inquiry mentioning saldo and relative days routes to get_journal_entries."""
        state = create_initial_state("aku mau tanya saldo di jurnal marketplace tiktok 3 hari terakhir")
        result = router_node(state)

        self.assertEqual(result["detected_domain"], "accounting")
        self.assertEqual(result["detected_tool"], "get_journal_entries")

    def test_inventory_stock_level_routing(self):
        """Stock query routes to get_stock_level."""
        state = create_initial_state("Cek stok kertas foto A4 di gudang utama")
        result = router_node(state)

        self.assertEqual(result["detected_domain"], "inventory")
        self.assertEqual(result["detected_tool"], "get_stock_level")

    def test_inventory_stock_level_with_suffix(self):
        """Query with Indonesian suffix 'stoknya' routes correctly to get_stock_level."""
        state = create_initial_state("kalau produk itu di mainstore berapa stoknya")
        result = router_node(state)

        self.assertEqual(result["detected_domain"], "inventory")
        self.assertEqual(result["detected_tool"], "get_stock_level")

    def test_inventory_locations_routing(self):
        """Locations/warehouse inquiries route to get_locations."""
        state1 = create_initial_state("Daftar gudang yang ada")
        self.assertEqual(router_node(state1)["detected_tool"], "get_locations")

        state2 = create_initial_state("Ada gudang apa saja di perusahaan ini?")
        self.assertEqual(router_node(state2)["detected_tool"], "get_locations")

        state3 = create_initial_state("list lokasi penyimpanan")
        self.assertEqual(router_node(state3)["detected_tool"], "get_locations")

    def test_inventory_product_info_routing(self):
        """Product query routes to get_product_info."""
        state = create_initial_state("Berapa harga jual dan barcode produk Tinta Sublim?")
        result = router_node(state)

        self.assertEqual(result["detected_domain"], "inventory")
        self.assertEqual(result["detected_tool"], "get_product_info")

    def test_empty_query_routing(self):
        """Empty query defaults to general domain and None tool."""
        state = create_initial_state("")
        result = router_node(state)

        self.assertEqual(result["detected_domain"], "general")
        self.assertIsNone(result["detected_tool"])

    def test_llm_fallback_routing_when_ambiguous(self):
        """When query is ambiguous, fallback to LLM structured output."""
        mock_llm = MagicMock()
        mock_structured = MagicMock()
        mock_structured.invoke.return_value = RouterDecision(
            domain="accounting",
            tool="get_account_balance",
            reasoning="User inquires about financial balance",
        )
        mock_llm.with_structured_output.return_value = mock_structured

        state = create_initial_state("Tolong bantu periksa status keuangan saya")
        result = router_node(state, llm=mock_llm)

        self.assertEqual(result["detected_domain"], "accounting")
        self.assertEqual(result["detected_tool"], "get_account_balance")
        mock_llm.with_structured_output.assert_called_once()


class TestExtractorNode(unittest.TestCase):
    """Test suite for extractor_node, registry, and date resolution."""

    def setUp(self):
        self.anchor_date = date(2026, 11, 4)

    def test_tool_schema_registry_coverage(self):
        """TOOL_SCHEMA_REGISTRY must register all 6 core tools."""
        self.assertIn("fetch_vendor_invoices", TOOL_SCHEMA_REGISTRY)
        self.assertIn("get_journal_entries", TOOL_SCHEMA_REGISTRY)
        self.assertIn("get_account_balance", TOOL_SCHEMA_REGISTRY)
        self.assertIn("get_stock_level", TOOL_SCHEMA_REGISTRY)
        self.assertIn("get_product_info", TOOL_SCHEMA_REGISTRY)
        self.assertIn("get_locations", TOOL_SCHEMA_REGISTRY)

        self.assertEqual(TOOL_SCHEMA_REGISTRY["fetch_vendor_invoices"], VendorInvoiceFilterSchema)
        self.assertEqual(TOOL_SCHEMA_REGISTRY["get_journal_entries"], JournalEntriesFilterSchema)
        self.assertEqual(TOOL_SCHEMA_REGISTRY["get_account_balance"], AccountBalanceFilterSchema)
        self.assertEqual(TOOL_SCHEMA_REGISTRY["get_stock_level"], StockLevelFilterSchema)
        self.assertEqual(TOOL_SCHEMA_REGISTRY["get_product_info"], ProductInfoFilterSchema)
        self.assertEqual(TOOL_SCHEMA_REGISTRY["get_locations"], LocationsFilterSchema)

    def test_dod_extractor_vendor_invoice_query(self):
        """
        Definition of Done: 'Cek tagihan vendor PT Sumber Makmur bulan ini'
        extracts vendor_name='PT Sumber Makmur' and resolves 'bulan ini' date range.
        """
        state = create_initial_state("Cek tagihan vendor PT Sumber Makmur bulan ini")
        state["detected_domain"] = "accounting"
        state["detected_tool"] = "fetch_vendor_invoices"

        result = extractor_node(state, ref_date=self.anchor_date)
        params = result.get("extracted_params", {})

        self.assertEqual(params.get("vendor_name"), "PT Sumber Makmur")
        self.assertEqual(params.get("date_from"), "2026-11-01")
        self.assertEqual(params.get("date_to"), "2026-11-30")

    def test_resolve_relative_dates_current_month(self):
        """'bulan ini' resolves to first and last day of anchor month."""
        date_from, date_to = resolve_relative_dates("cek transaksi bulan ini", ref_date=self.anchor_date)
        self.assertEqual(date_from, "2026-11-01")
        self.assertEqual(date_to, "2026-11-30")

    def test_resolve_relative_dates_today(self):
        """'hari ini' resolves to anchor date for both from and to."""
        date_from, date_to = resolve_relative_dates("cek transaksi hari ini", ref_date=self.anchor_date)
        self.assertEqual(date_from, "2026-11-04")
        self.assertEqual(date_to, "2026-11-04")

    def test_resolve_relative_dates_yesterday(self):
        """'kemarin' resolves to previous day."""
        date_from, date_to = resolve_relative_dates("cek invoice kemarin", ref_date=self.anchor_date)
        self.assertEqual(date_from, "2026-11-03")
        self.assertEqual(date_to, "2026-11-03")

    def test_resolve_explicit_dates(self):
        """Explicit YYYY-MM-DD dates in query are preserved."""
        query = "filter dari 2026-03-01 sampai 2026-03-31"
        date_from, date_to = resolve_relative_dates(query, ref_date=self.anchor_date)
        self.assertEqual(date_from, "2026-03-01")
        self.assertEqual(date_to, "2026-03-31")

    def test_extractor_with_mock_llm_structured_output(self):
        """Extractor properly invokes LLM structured output when provided."""
        mock_llm = MagicMock()
        mock_structured = MagicMock()
        mock_structured.invoke.return_value = VendorInvoiceFilterSchema(
            vendor_name="PT Sumber Makmur",
            date_from="2026-11-01",
            date_to="2026-11-30",
            status="unpaid",
        )
        mock_llm.with_structured_output.return_value = mock_structured

        state = create_initial_state("Cek tagihan vendor PT Sumber Makmur bulan ini")
        state["detected_tool"] = "fetch_vendor_invoices"

        result = extractor_node(state, llm=mock_llm, ref_date=self.anchor_date)
        params = result.get("extracted_params", {})

        self.assertEqual(params.get("vendor_name"), "PT Sumber Makmur")
        self.assertEqual(params.get("date_from"), "2026-11-01")
        self.assertEqual(params.get("date_to"), "2026-11-30")
        self.assertEqual(params.get("status"), "unpaid")

    def test_extractor_resilience_missing_tool(self):
        """When detected_tool is None, extractor safely returns empty dict."""
        state = create_initial_state("Halo selamat pagi")
        state["detected_tool"] = None

        result = extractor_node(state)
        self.assertEqual(result, {"extracted_params": {}})

    def test_extractor_stock_level_with_location_and_history(self):
        """Extractor extracts location_name and resolves product code from conversation history."""
        # Simulated previous turn: User asks about AAEO-NPKG, assistant answers with SKU 3443 / AAEO-NPKG
        history = [
            HumanMessage(content="saya mau tanya stok barang AAEO-NPKG"),
            AIMessage(content="### Informasi Ketersediaan Stok Barang\nSKU 3443 AAEO-NPKG Qty 1.05 unit"),
        ]
        state = create_initial_state(
            user_query="kalau produk itu di mainstore berapa stoknya",
            messages=history + [HumanMessage(content="kalau produk itu di mainstore berapa stoknya")],
        )
        state["detected_domain"] = "inventory"
        state["detected_tool"] = "get_stock_level"

        result = extractor_node(state)
        params = result.get("extracted_params", {})

        self.assertEqual(params.get("code"), "3443")
        self.assertEqual(params.get("location_name"), "mainstore")

    def test_extractor_get_locations_query(self):
        """Extractor extracts search keyword for get_locations."""
        state = create_initial_state("daftar gudang utama")
        state["detected_domain"] = "inventory"
        state["detected_tool"] = "get_locations"

        result = extractor_node(state)
        params = result.get("extracted_params", {})
        self.assertEqual(params.get("search"), "utama")

    def test_extractor_stock_preposition_not_extracted_as_sku(self):
        """
        R3: Indonesian prepositions ('di', 'ke', 'dari', 'pada', 'untuk', 'terkait', 'tentang')
        must not be extracted as SKU codes in stock level queries.
        """
        # Query 1: 'cek stok di gudang utama' -> location_name='utama', code is None
        state1 = create_initial_state("cek stok di gudang utama")
        state1["detected_domain"] = "inventory"
        state1["detected_tool"] = "get_stock_level"
        params1 = extractor_node(state1).get("extracted_params", {})
        self.assertEqual(params1.get("location_name"), "utama")
        self.assertIsNone(params1.get("code"))

        # Query 2: 'cek stok ke cabang' -> code is None
        state2 = create_initial_state("cek stok ke cabang")
        state2["detected_domain"] = "inventory"
        state2["detected_tool"] = "get_stock_level"
        params2 = extractor_node(state2).get("extracted_params", {})
        self.assertIsNone(params2.get("code"))

        # Query 3: 'cek stok pada rak 2' -> code is None
        state3 = create_initial_state("cek stok pada rak 2")
        state3["detected_domain"] = "inventory"
        state3["detected_tool"] = "get_stock_level"
        params3 = extractor_node(state3).get("extracted_params", {})
        self.assertIsNone(params3.get("code"))

        # Query 4: 'cek stok dari gudang' -> code is None
        state4 = create_initial_state("cek stok dari gudang")
        state4["detected_domain"] = "inventory"
        state4["detected_tool"] = "get_stock_level"
        params4 = extractor_node(state4).get("extracted_params", {})
        self.assertIsNone(params4.get("code"))

        # Query 5: 'cek stok di gudang utama untuk AAEO-NPKG' -> location_name='utama', code='AAEO-NPKG'
        state5 = create_initial_state("cek stok di gudang utama untuk AAEO-NPKG")
        state5["detected_domain"] = "inventory"
        state5["detected_tool"] = "get_stock_level"
        params5 = extractor_node(state5).get("extracted_params", {})
        self.assertEqual(params5.get("location_name"), "utama")
        self.assertEqual(params5.get("code"), "AAEO-NPKG")

        # Query 6: 'cek stok terkait AAEO-NPKG' -> code='AAEO-NPKG'
        state6 = create_initial_state("cek stok terkait AAEO-NPKG")
        state6["detected_domain"] = "inventory"
        state6["detected_tool"] = "get_stock_level"
        params6 = extractor_node(state6).get("extracted_params", {})
        self.assertEqual(params6.get("code"), "AAEO-NPKG")

        # Query 7: 'cek stok tentang AAEO-NPKG' -> code='AAEO-NPKG'
        state7 = create_initial_state("cek stok tentang AAEO-NPKG")
        state7["detected_domain"] = "inventory"
        state7["detected_tool"] = "get_stock_level"
        params7 = extractor_node(state7).get("extracted_params", {})
        self.assertEqual(params7.get("code"), "AAEO-NPKG")

        # Query 8: 'cek sisa stok untuk ART-PPR-210' -> code='ART-PPR-210' (sisa and stok in stopwords)
        state8 = create_initial_state("cek sisa stok untuk ART-PPR-210")
        state8["detected_domain"] = "inventory"
        state8["detected_tool"] = "get_stock_level"
        params8 = extractor_node(state8).get("extracted_params", {})
        self.assertEqual(params8.get("code"), "ART-PPR-210")

        # Query 9: 'cek stok di gudang untuk customer Andi' -> location_name must not be 'untuk'
        state9 = create_initial_state("cek stok di gudang untuk customer Andi")
        state9["detected_domain"] = "inventory"
        state9["detected_tool"] = "get_stock_level"
        params9 = extractor_node(state9).get("extracted_params", {})
        self.assertNotEqual(params9.get("location_name"), "untuk")

    def test_extractor_vendor_delimiters_trim_trailing_words(self):
        """
        R9: Delimiters like 'yang', 'belum', 'lunas', 'semua', 'sudah', 'dan' must stop vendor name
        extraction and prevent trailing fragment absorption.
        """
        cases = [
            ("tagihan vendor PT Sumber Makmur yang belum lunas", "PT Sumber Makmur", "unpaid"),
            ("faktur vendor CV Abadi Jaya belum lunas", "CV Abadi Jaya", "unpaid"),
            ("semua tagihan supplier Toko Kertas semua", "Toko Kertas", None),
            ("Faktur PT Sumber Makmur yang belum dibayar", "PT Sumber Makmur", "unpaid"),
            ("daftar tagihan vendor PT Cahaya semua", "PT Cahaya", None),
            ("tagihan vendor CV Abadi Jaya lunas", "CV Abadi Jaya", "paid"),
            ("faktur vendor UD Maju Bersama sudah dibayar", "UD Maju Bersama", "paid"),
            ("faktur vendor Toko Kertas dan status lunas", "Toko Kertas", "paid"),
        ]
        for query, expected_vendor, expected_status in cases:
            state = create_initial_state(query)
            state["detected_domain"] = "accounting"
            state["detected_tool"] = "fetch_vendor_invoices"
            params = extractor_node(state, ref_date=self.anchor_date).get("extracted_params", {})
            self.assertEqual(params.get("vendor_name"), expected_vendor, f"Failed vendor extraction for '{query}'")
            if expected_status:
                self.assertEqual(params.get("status"), expected_status, f"Failed status extraction for '{query}'")


class TestLangGraphPipelineChain(unittest.TestCase):
    """Integration test chaining router_node and extractor_node in LangGraph StateGraph."""

    def test_end_to_end_router_extractor_cycle(self):
        """Chains router_node -> extractor_node in StateGraph and verifies DoD output."""
        anchor_date = date(2026, 11, 4)

        def wrapped_router(state: AgentState) -> dict:
            return router_node(state)

        def wrapped_extractor(state: AgentState) -> dict:
            return extractor_node(state, ref_date=anchor_date)

        builder = StateGraph(AgentState)
        builder.add_node("router", wrapped_router)
        builder.add_node("extractor", wrapped_extractor)

        builder.add_edge(START, "router")
        builder.add_edge("router", "extractor")
        builder.add_edge("extractor", END)

        graph = builder.compile()

        # Run DoD test query
        initial_state = create_initial_state("Cek tagihan vendor PT Sumber Makmur bulan ini")
        final_state = graph.invoke(initial_state)

        # Verify router output
        self.assertEqual(final_state["detected_domain"], "accounting")
        self.assertEqual(final_state["detected_tool"], "fetch_vendor_invoices")

        # Verify extractor output (DoD)
        params = final_state.get("extracted_params", {})
        self.assertEqual(params.get("vendor_name"), "PT Sumber Makmur")
        self.assertEqual(params.get("date_from"), "2026-11-01")
        self.assertEqual(params.get("date_to"), "2026-11-30")

    def test_end_to_end_customer_inventory_chain(self):
        """
        R2 + R3 integration: Customer inventory stock check routes to inventory
        and extracts location without confusing preposition with code.
        """
        anchor_date = date(2026, 11, 4)

        builder = StateGraph(AgentState)
        builder.add_node("router", router_node)
        builder.add_node("extractor", lambda s: extractor_node(s, ref_date=anchor_date))

        builder.add_edge(START, "router")
        builder.add_edge("router", "extractor")
        builder.add_edge("extractor", END)

        graph = builder.compile()

        state = create_initial_state("Cek stok di gudang utama untuk customer Budi")
        final_state = graph.invoke(state)

        self.assertEqual(final_state["detected_domain"], "inventory")
        self.assertEqual(final_state["detected_tool"], "get_stock_level")
        params = final_state.get("extracted_params", {})
        self.assertEqual(params.get("location_name"), "utama")
        self.assertIsNone(params.get("code"))


if __name__ == "__main__":
    unittest.main()
