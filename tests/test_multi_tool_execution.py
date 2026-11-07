"""
End-to-End and Component Test Suite for Multi-Tool & Cross-Module Execution in FastPrint ERP LangGraph Orchestrator.
Validates Supervisor Routing, Parameter Extraction across multiple schemas, Concurrent Tool Dispatch,
Aggregate Verification, and Unified Markdown Synthesis.
"""
import asyncio
from datetime import date
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.graph.state import AgentState, ToolCallPlan, create_initial_state
from app.graph.nodes.router import router_node, classify_query_rule_based
from app.graph.nodes.extractor import extractor_node
from app.graph.nodes.tool_node import tool_node, tool_node_sync
from app.graph.nodes.verifier import verifier_node
from app.graph.nodes.synthesizer import synthesizer_node, format_deterministic_answer
from app.graph.workflow import create_workflow_graph
from app.services.django_client import DjangoCoreClient


class TestMultiToolExecution(unittest.TestCase):
    """Test suite validating end-to-end multi-tool execution across ERP domains."""

    def test_multi_domain_routing_stock_and_balance(self):
        """Router correctly classifies a multi-intent query spanning inventory stock and cash balance."""
        query = "Berapa stok Kertas Art Paper dan berapa saldo kas bulan ini?"
        state = create_initial_state(user_query=query, company_id=1)

        result = router_node(state)

        self.assertTrue(result.get("is_multi_tool"))
        active_tools = result.get("active_tools") or []
        self.assertGreaterEqual(len(active_tools), 2)
        tool_names = [p.get("tool") for p in active_tools]
        self.assertIn("get_stock_level", tool_names)
        self.assertIn("get_account_balance", tool_names)

    def test_multi_tool_parameter_extraction(self):
        """Extractor parses tool-specific parameters for each planned tool in active_tools."""
        active_tools = [
            {"tool": "get_stock_level", "domain": "inventory", "sub_query": "stok Kertas Art Paper", "params": {}},
            {"tool": "get_account_balance", "domain": "accounting", "sub_query": "saldo kas bulan ini", "params": {}},
        ]
        state: AgentState = {
            "user_query": "Berapa stok Kertas Art Paper dan berapa saldo kas bulan ini?",
            "is_multi_tool": True,
            "active_tools": active_tools,
            "company_id": 1,
            "retry_count": 0,
        }

        fixed_today = date(2026, 11, 7)
        result = extractor_node(state, ref_date=fixed_today)

        updated_plans = result.get("active_tools") or []
        self.assertEqual(len(updated_plans), 2)

        stock_plan = next(p for p in updated_plans if p.get("tool") == "get_stock_level")
        balance_plan = next(p for p in updated_plans if p.get("tool") == "get_account_balance")

        # Stock plan parameters
        self.assertIn("code", stock_plan["params"])
        self.assertEqual(stock_plan["params"]["code"], "Kertas")

        # Accounting balance plan parameters (relative date resolved to current month)
        self.assertEqual(balance_plan["params"].get("date_from"), "2026-11-01")
        self.assertEqual(balance_plan["params"].get("date_to"), "2026-11-30")

    def test_concurrent_tool_dispatch(self):
        """Tool node concurrently executes all planned tools via asyncio.gather and stores results."""
        mock_client = AsyncMock(spec=DjangoCoreClient)
        mock_client.fetch_stock_level.return_value = [
            {"code": "AP-210", "name": "Kertas Art Paper 210", "quantity": 150.0, "company_id": 1}
        ]
        mock_client.fetch_account_balance.return_value = {
            "net_balance": 25000000.0,
            "total_debit": 50000000.0,
            "total_credit": 25000000.0,
        }

        active_tools = [
            {"tool": "get_stock_level", "domain": "inventory", "params": {"search": "AP-210", "company_id": 1}},
            {"tool": "get_account_balance", "domain": "accounting", "params": {"company_id": 1}},
        ]
        state: AgentState = {
            "user_query": "Cek stok dan saldo",
            "is_multi_tool": True,
            "active_tools": active_tools,
            "company_id": 1,
        }

        res = asyncio.run(tool_node(state, client=mock_client))

        self.assertEqual(res.get("verification_status"), "VALID")
        multi_results = res.get("multi_tool_results") or {}
        self.assertIn("get_stock_level", multi_results)
        self.assertIn("get_account_balance", multi_results)
        self.assertEqual(multi_results["get_stock_level"][0]["quantity"], 150.0)
        self.assertEqual(multi_results["get_account_balance"]["net_balance"], 25000000.0)

        # Plan status updated
        for plan in res.get("active_tools") or []:
            self.assertEqual(plan.get("status"), "success")

    def test_aggregate_verifier_all_valid(self):
        """Aggregate verifier sets VALID when all tools return records."""
        state: AgentState = {
            "is_multi_tool": True,
            "multi_tool_results": {
                "get_stock_level": [{"sku": "AP-210", "qty_on_hand": 50}],
                "get_account_balance": {"net_balance": 10000000.0, "total_debit": 10000000.0, "total_credit": 0.0},
            },
            "retry_count": 0,
        }

        res = verifier_node(state)
        self.assertEqual(res.get("verification_status"), "VALID")
        self.assertEqual(res.get("multi_verification_status", {}).get("get_stock_level"), "VALID")
        self.assertEqual(res.get("multi_verification_status", {}).get("get_account_balance"), "VALID")

    def test_aggregate_verifier_partial_empty_triggers_retry(self):
        """Aggregate verifier triggers RETRY when one tool returns empty results and retry budget remains."""
        state: AgentState = {
            "is_multi_tool": True,
            "multi_tool_results": {
                "get_stock_level": [{"sku": "AP-210", "qty_on_hand": 50}],
                "get_account_balance": {"net_balance": 0.0, "total_debit": 0.0, "total_credit": 0.0},
            },
            "retry_count": 0,
        }

        res = verifier_node(state)
        self.assertEqual(res.get("verification_status"), "RETRY")
        self.assertEqual(res.get("retry_count"), 1)
        self.assertIn("date_from", res.get("retry_instruction", ""))

    def test_aggregate_verifier_partial_empty_exhausted_retry_returns_valid(self):
        """Aggregate verifier returns VALID when retries exhausted but at least one tool has data."""
        state: AgentState = {
            "is_multi_tool": True,
            "multi_tool_results": {
                "get_stock_level": [{"sku": "AP-210", "qty_on_hand": 50}],
                "get_account_balance": {"net_balance": 0.0, "total_debit": 0.0, "total_credit": 0.0},
            },
            "retry_count": 2,
        }

        res = verifier_node(state)
        self.assertEqual(res.get("verification_status"), "VALID")

    def test_unified_synthesizer_multi_tool(self):
        """Synthesizer formats a combined markdown response with dedicated sections for both tools."""
        state: AgentState = {
            "user_query": "Berapa stok Kertas Art Paper dan berapa saldo kas?",
            "is_multi_tool": True,
            "verification_status": "VALID",
            "active_tools": [
                {"tool": "get_stock_level", "domain": "inventory"},
                {"tool": "get_account_balance", "domain": "accounting"},
            ],
            "multi_tool_results": {
                "get_stock_level": [
                    {"code": "AP-210", "name": "Kertas Art Paper 210", "qty_on_hand": 150.0, "company_id": 1}
                ],
                "get_account_balance": {
                    "net_balance": 25000000.0,
                    "total_debit": 50000000.0,
                    "total_credit": 25000000.0,
                },
            },
        }

        output = format_deterministic_answer(state)

        # Asserts both inventory table and accounting section exist
        self.assertIn("Informasi Ketersediaan Stok Barang (Inventory)", output)
        self.assertIn("Kertas Art Paper 210", output)
        self.assertIn("150.0 unit", output)
        self.assertIn("Informasi Saldo Chart of Account (Accounting Balance)", output)
        self.assertIn("Rp 25.000.000,00", output)
        self.assertIn("---", output)

    def test_full_pipeline_multi_tool_end_to_end(self):
        """End-to-end multi-tool pipeline executes through StateGraph and produces a unified report."""
        mock_client = AsyncMock(spec=DjangoCoreClient)
        mock_client.fetch_stock_level.return_value = [
            {"code": "AP-210", "name": "Kertas Art Paper 210", "qty_on_hand": 100.0, "company_id": 1}
        ]
        mock_client.fetch_account_balance.return_value = {
            "net_balance": 15000000.0,
            "total_debit": 30000000.0,
            "total_credit": 15000000.0,
        }

        app = create_workflow_graph()

        initial_state = create_initial_state(
            user_query="Cek stok Kertas Art Paper dan saldo kas bulan ini",
            company_id=1,
        )

        thread_cfg = {"configurable": {"thread_id": "test_multi_tool_thread"}}
        with patch("app.graph.nodes.tool_node.DjangoCoreClient", return_value=mock_client):
            final_state = asyncio.run(app.ainvoke(initial_state, config=thread_cfg))

        self.assertEqual(final_state.get("verification_status"), "VALID")
        final_answer = final_state.get("final_answer") or ""

        # Verify output combines stock and accounting balance
        self.assertIn("Kertas Art Paper", final_answer)
        self.assertIn("100.0 unit", final_answer)
        self.assertIn("Rp 15.000.000,00", final_answer)


if __name__ == "__main__":
    unittest.main()
