"""
Unit tests for Branch Entity Resolution, Strict Date Handling, and Vendor Filter Passthrough.
"""
import unittest
from datetime import date
from langchain_core.messages import HumanMessage

from app.graph.state import create_initial_state
from app.graph.nodes.extractor import (
    resolve_branch_company_id,
    has_temporal_expression,
    extractor_node,
    ALLOWED_JOURNAL_STATUSES,
)
from app.schemas.tools import (
    VendorInvoiceFilterSchema,
    JournalEntriesFilterSchema,
)


class TestBranchAndVendorExtractor(unittest.TestCase):
    """Test suite for branch resolution, strict date extraction, and vendor parameters."""

    def test_resolve_branch_company_id(self):
        """Test Indonesian branch and city name resolution to ERP company_id."""
        cases = [
            ("coba cek lagi tapi dicabang bandung", 6),
            ("cek tagihan vendor MEGACOM BEC dicabang bandung", 6),
            ("cek stok di cabang surabaya", 7),
            ("cek transaksi di kantor pusat", 1),
            ("laporan di head office", 3),
            ("stok di main store", 4),
            ("tagihan di makassar", 5),
            ("tagihan di fp makasar", 5),
            ("cek cabang jakarta", 8),
            ("cek toko di medan", 10),
            ("cek di bekasi", 13),
            ("cek cabang tangerang", 14),
            ("stok malang", 15),
            ("transaksi fastmart", 17),
            ("coba cek tagihan vendor MEGACOM BEC", None),
        ]
        for query, expected_id in cases:
            with self.subTest(query=query):
                actual_id = resolve_branch_company_id(query)
                self.assertEqual(actual_id, expected_id, f"Failed for query: '{query}'")

    def test_has_temporal_expression(self):
        """Test detection of temporal expressions in query text."""
        # Queries without temporal expressions
        no_temporal = [
            "coba cek tagihan vendor MEGACOM BEC",
            "coba cek lagi tapi dicabang bandung",
            "cek stok toner hp",
            "berapa saldo kas kecil",
        ]
        for query in no_temporal:
            with self.subTest(query=query):
                self.assertFalse(has_temporal_expression(query), f"Should not have temporal: '{query}'")

        # Queries with temporal expressions
        has_temporal = [
            "cek tagihan bulan ini",
            "transaksi bulan lalu",
            "tagihan kemarin",
            "cek transaksi agustus 2026",
            "transaksi tanggal 2026-08-11",
            "cek 3 hari terakhir",
            "transaksi sejak 2026-01-01 hingga 2026-06-30",
        ]
        for query in has_temporal:
            with self.subTest(query=query):
                self.assertTrue(has_temporal_expression(query), f"Should have temporal: '{query}'")

    def test_vendor_invoice_schema_to_journal_filter(self):
        """Test that VendorInvoiceFilterSchema propagates vendor_name and vendor_id to journal filter."""
        schema = VendorInvoiceFilterSchema(
            vendor_name="MEGACOM BEC",
            vendor_id=583999,
            company_id=6,
            status="draft",
            date_from="2026-08-01",
            date_to="2026-08-31",
            search=None,
        )
        journal_filter = schema.to_journal_filter()
        self.assertIsInstance(journal_filter, JournalEntriesFilterSchema)
        self.assertEqual(journal_filter.vendor_name, "MEGACOM BEC")
        self.assertEqual(journal_filter.vendor_id, 583999)
        self.assertEqual(journal_filter.company_id, 6)
        self.assertEqual(journal_filter.status, "draft")

    def test_extractor_node_branch_resolution(self):
        """Test extractor_node resolves company_id from user query and sets it in state."""
        state = create_initial_state(
            user_query="coba cek tagihan vendor MEGACOM BEC dicabang bandung",
            company_id=1,  # Initial CLI company is 1
        )
        state["detected_domain"] = "accounting"
        state["detected_tool"] = "fetch_vendor_invoices"

        result = extractor_node(state, llm=None, ref_date=date(2026, 11, 8))

        # Check company_id in state update
        self.assertEqual(result.get("company_id"), 6)

        # Check extracted_params
        params = result.get("extracted_params", {})
        self.assertEqual(params.get("company_id"), 6)
        self.assertEqual(params.get("vendor_name"), "MEGACOM BEC")

        # Strict date: no date_from / date_to because user didn't mention any date
        self.assertIsNone(params.get("date_from"))
        self.assertIsNone(params.get("date_to"))

    def test_status_sanitization_drops_invalid_status(self):
        """Test extractor sanitizes invalid status values like 'not_found'."""
        self.assertNotIn("not_found", ALLOWED_JOURNAL_STATUSES)
        self.assertIn("draft", ALLOWED_JOURNAL_STATUSES)
        self.assertIn("posted", ALLOWED_JOURNAL_STATUSES)
        self.assertIn("unpaid", ALLOWED_JOURNAL_STATUSES)


if __name__ == "__main__":
    unittest.main()
