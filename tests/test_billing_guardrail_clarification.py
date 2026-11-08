"""
Unit tests for Billing Guardrail Clarification in Synthesizer Node.
Validates that ambiguous or customer-oriented billing queries (such as 'coba cek tagihan Frendy Krisnata di mainstore')
receive clear educational guardrail guidance explaining vendor vs customer scope and directing to Sales/POS,
instead of falling back to a blank generic greeting.
"""
import unittest
from app.graph.state import create_initial_state
from app.graph.nodes.synthesizer import (
    synthesizer_node,
    format_deterministic_answer,
)


class TestBillingGuardrailClarification(unittest.TestCase):
    """Test suite for billing queries routed to general guardrail."""

    def test_customer_name_billing_query_triggers_informative_guardrail(self):
        """
        Query: 'coba cek tagihan Frendy Krisnata di mainstore'
        Should produce clear guardrail guidance directing to Sales/Invoicing and vendor hint.
        """
        state = create_initial_state(user_query="coba cek tagihan Frendy Krisnata di mainstore")
        state["detected_domain"] = "general"
        state["detected_tool"] = None

        output = format_deterministic_answer(state)
        self.assertIn("Tagihan Pelanggan (Customer)", output)
        self.assertIn("Penjualan (Sales / Invoicing)", output)
        self.assertIn("tagihan vendor", output)
        self.assertNotIn("Silakan sampaikan pertanyaan spesifik Anda!", output)

    def test_various_billing_terms_trigger_guardrail_guidance(self):
        """Queries containing invoice/faktur/tagihan without explicit 'pelanggan' keyword."""
        test_queries = [
            "cek tagihan Frendy",
            "faktur pembelian atau penjualan toko mainstore",
            "tolong periksa invoice atas nama Hendra",
            "status bill pesanan kemarin",
            "cek bon tagihan toko",
            "kuitansi transaksi Andi",
        ]
        for query in test_queries:
            with self.subTest(query=query):
                state = create_initial_state(user_query=query)
                state["detected_domain"] = "general"
                state["detected_tool"] = None

                output = format_deterministic_answer(state)
                self.assertIn("Tagihan Pelanggan (Customer)", output)
                self.assertIn("Penjualan (Sales / Invoicing)", output)
                self.assertNotIn("Silakan sampaikan pertanyaan spesifik Anda!", output)

    def test_pure_greetings_still_produce_standard_menu(self):
        """Pure greetings and capability requests should still show the standard menu."""
        greetings = [
            "Halo!",
            "Selamat pagi",
            "Bisa bantu apa saja kamu?",
            "Hai bot",
            "Assalamu'alaikum",
        ]
        for g in greetings:
            with self.subTest(greeting=g):
                state = create_initial_state(user_query=g)
                state["detected_domain"] = "general"
                state["detected_tool"] = None

                output = format_deterministic_answer(state)
                self.assertIn("Halo! Saya adalah Asisten AI FastPrint ERP", output)
                self.assertIn("Silakan sampaikan pertanyaan spesifik Anda!", output)

    def test_synthesizer_node_full_turn_with_aimessage(self):
        """Verify full synthesizer_node invocation appends AIMessage with informative answer."""
        state = create_initial_state(user_query="coba cek tagihan Frendy Krisnata di mainstore")
        state["detected_domain"] = "general"
        state["detected_tool"] = None

        result = synthesizer_node(state)
        self.assertIn("final_answer", result)
        self.assertIn("Tagihan Pelanggan (Customer)", result["final_answer"])
        self.assertEqual(len(result["messages"]), 1)
        self.assertIn("Tagihan Pelanggan (Customer)", result["messages"][0].content)


if __name__ == "__main__":
    unittest.main()
