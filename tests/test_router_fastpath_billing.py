"""
Unit tests for Fast-Path Non-Vendor Billing Guardrail in Router Node.
Validates that queries asking for bills/invoices without vendor keywords
(e.g. 'coba cek tagihan Frendy Krisnata di mainstore') trigger the local
deterministic rule-based guardrail in <5ms without calling remote LLMs.
"""
import time
import unittest

from app.graph.nodes.router import classify_query_rule_based, router_node
from app.graph.state import create_initial_state


class TestRouterFastpathBilling(unittest.TestCase):
    """Test suite for deterministic fast-path non-vendor billing guardrail."""

    def test_customer_name_billing_triggers_fastpath(self):
        """
        Query: 'coba cek tagihan Frendy Krisnata di mainstore'
        Must trigger rule-based classifier locally without returning None.
        """
        query = "coba cek tagihan Frendy Krisnata di mainstore"
        t0 = time.time()
        result = classify_query_rule_based(query)
        elapsed_ms = (time.time() - t0) * 1000

        self.assertIsNotNone(result, "Fast-path rule must classify billing query without LLM fallback")
        self.assertEqual(result["detected_domain"], "general")
        self.assertIsNone(result["detected_tool"])
        self.assertEqual(result["query_mode"], "general")
        self.assertLess(elapsed_ms, 50, f"Fast-path should execute in <50ms (got {elapsed_ms:.2f}ms)")

    def test_various_non_vendor_billing_queries_trigger_fastpath(self):
        """Queries with invoice/tagihan/faktur but no vendor keywords."""
        queries = [
            "cek tagihan Frendy",
            "faktur penjualan toko mainstore",
            "tolong periksa invoice atas nama Hendra",
            "status bill toko cabang Surabaya",
            "cek bon tagihan kasir",
            "kuitansi transaksi Andi",
            "berapa tagihan customer Budi",
        ]
        for q in queries:
            with self.subTest(query=q):
                res = classify_query_rule_based(q)
                self.assertIsNotNone(res, f"Query '{q}' should be classified by rule-based fast-path")
                self.assertEqual(res["detected_domain"], "general")
                self.assertIsNone(res["detected_tool"])

    def test_vendor_billing_queries_still_route_to_fetch_vendor_invoices(self):
        """Vendor billing queries must continue routing to fetch_vendor_invoices."""
        vendor_queries = [
            "coba cek tagihan vendor Frendy Krisnata di mainstore",
            "cek tagihan supplier PT Sumber Makmur",
            "faktur pembelian dari CV Abadi Jaya",
            "hutang vendor Toko Kertas",
        ]
        for q in vendor_queries:
            with self.subTest(query=q):
                res = classify_query_rule_based(q)
                self.assertIsNotNone(res, f"Vendor query '{q}' should match rule-based")
                self.assertEqual(res["detected_domain"], "accounting")
                self.assertEqual(res["detected_tool"], "fetch_vendor_invoices")

    def test_router_node_with_fastpath_query(self):
        """Verify full router_node execution with fast-path query."""
        state = create_initial_state(user_query="coba cek tagihan Frendy Krisnata di mainstore")
        result = router_node(state)
        self.assertEqual(result["detected_domain"], "general")
        self.assertIsNone(result["detected_tool"])
        self.assertEqual(result["query_mode"], "general")


if __name__ == "__main__":
    unittest.main()
