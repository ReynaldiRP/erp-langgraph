"""
Unit tests for Tool Node execution (TASK-4.3).
Validates dynamic tool dispatching, user token forwarding, multi-company priority scoping,
preservation of transaction data and calculations block (DoD), graceful error handling,
sync wrapper execution, and full 3-node LangGraph pipeline integration.
"""
from datetime import date
import unittest
from unittest.mock import AsyncMock, MagicMock
from langgraph.graph import StateGraph, START, END

from app.graph.state import create_initial_state, AgentState
from app.graph.nodes.router import router_node
from app.graph.nodes.extractor import extractor_node
from app.graph.nodes.tool_node import tool_node, tool_node_sync, _execute_single_tool
from app.services.django_client import (
    DjangoCoreClient,
    DjangoAuthError,
    DjangoPermissionError,
    DjangoServerError,
    DjangoCoreClientError,
)


class TestToolNodeDoDAndPayloadPreservation(unittest.IsolatedAsyncioTestCase):
    """Test suite validating transaction data and calculations block preservation (DoD)."""

    async def test_dod_vendor_invoices_data_and_calculations_preserved(self):
        """
        Definition of Done:
        Data transaksi dan blok calculations dari Django tersimpan utuh di dalam state.
        """
        mock_payload = {
            "total_count": 2,
            "entries": [
                {
                    "id": 101,
                    "number": "BILL/2026/001",
                    "date": "2026-11-05",
                    "vendor_id": 10,
                    "vendor_name": "PT Sumber Makmur",
                    "total_debit": 5000000.0,
                    "total_credit": 5000000.0,
                    "status": "unpaid",
                },
                {
                    "id": 102,
                    "number": "BILL/2026/002",
                    "date": "2026-11-12",
                    "vendor_id": 10,
                    "vendor_name": "PT Sumber Makmur",
                    "total_debit": 3500000.0,
                    "total_credit": 3500000.0,
                    "status": "unpaid",
                },
            ],
            "calculations": {
                "total_unpaid": 8500000.0,
                "total_paid": 0.0,
                "grand_total": 8500000.0,
                "invoice_count": 2,
            },
        }

        mock_client = MagicMock(spec=DjangoCoreClient)
        mock_client.fetch_vendor_invoices = AsyncMock(return_value=mock_payload)

        state = create_initial_state("Cek tagihan vendor PT Sumber Makmur bulan ini")
        state["detected_domain"] = "accounting"
        state["detected_tool"] = "fetch_vendor_invoices"
        state["extracted_params"] = {
            "vendor_name": "PT Sumber Makmur",
            "date_from": "2026-11-01",
            "date_to": "2026-11-30",
        }
        state["user_token"] = "jwt-access-token-xyz"
        state["company_id"] = 1

        result = await tool_node(state, client=mock_client)

        # 1. Verification of DoD: calculations block is intact
        tool_result = result["tool_result"]
        self.assertIsNotNone(tool_result)
        self.assertIn("calculations", tool_result)
        self.assertEqual(tool_result["calculations"]["grand_total"], 8500000.0)
        self.assertEqual(tool_result["calculations"]["total_unpaid"], 8500000.0)
        self.assertEqual(tool_result["calculations"]["invoice_count"], 2)

        # 2. Verification of DoD: transaction entries are intact
        self.assertIn("entries", tool_result)
        self.assertEqual(len(tool_result["entries"]), 2)
        self.assertEqual(tool_result["entries"][0]["vendor_name"], "PT Sumber Makmur")

        # 3. Status checks
        self.assertEqual(result["verification_status"], "VALID")
        self.assertIsNone(result["error_message"])


class TestSecurityAndCompanyScoping(unittest.IsolatedAsyncioTestCase):
    """Test suite validating user token forwarding and multi-company priority scoping."""

    async def test_user_token_forwarded_to_client(self):
        """User token from state is forwarded into client call."""
        mock_client = MagicMock(spec=DjangoCoreClient)
        mock_client.fetch_vendor_invoices = AsyncMock(return_value={"entries": []})

        state = create_initial_state("Cek tagihan")
        state["user_token"] = "secure-user-token-123"
        state["detected_tool"] = "fetch_vendor_invoices"

        await tool_node(state, client=mock_client)

        mock_client.fetch_vendor_invoices.assert_called_once()
        _, kwargs = mock_client.fetch_vendor_invoices.call_args
        self.assertEqual(kwargs.get("user_token"), "secure-user-token-123")

    async def test_query_company_id_takes_priority_over_session(self):
        """When query extracted specific company_id, it overrides session company_id."""
        mock_client = MagicMock(spec=DjangoCoreClient)
        mock_client.fetch_vendor_invoices = AsyncMock(return_value={"entries": []})

        state = create_initial_state("Cek tagihan vendor untuk mainstore (company 2)")
        state["company_id"] = 1  # Session company (e.g. Headoffice)
        state["detected_tool"] = "fetch_vendor_invoices"
        state["extracted_params"] = {"company_id": 2, "vendor_name": "PT Mitra"}

        await tool_node(state, client=mock_client)

        _, kwargs = mock_client.fetch_vendor_invoices.call_args
        self.assertEqual(kwargs.get("company_id"), 2)

    async def test_fallback_to_session_company_id_when_query_omits_it(self):
        """When query omits company_id, fall back to session state company_id."""
        mock_client = MagicMock(spec=DjangoCoreClient)
        mock_client.fetch_vendor_invoices = AsyncMock(return_value={"entries": []})

        state = create_initial_state("Cek tagihan vendor")
        state["company_id"] = 5
        state["detected_tool"] = "fetch_vendor_invoices"
        state["extracted_params"] = {"vendor_name": "PT Mitra"}

        await tool_node(state, client=mock_client)

        _, kwargs = mock_client.fetch_vendor_invoices.call_args
        self.assertEqual(kwargs.get("company_id"), 5)


class TestGracefulErrorHandling(unittest.IsolatedAsyncioTestCase):
    """Test suite validating graceful error capture (no unhandled graph crashes)."""

    async def test_auth_error_401_gracefully_captured(self):
        """HTTP 401 error sets verification_status='ERROR' and populates error_message."""
        mock_client = MagicMock(spec=DjangoCoreClient)
        mock_client.fetch_vendor_invoices = AsyncMock(
            side_effect=DjangoAuthError("Sesi token JWT kadaluarsa (HTTP 401)")
        )

        state = create_initial_state("Cek tagihan vendor")
        state["detected_tool"] = "fetch_vendor_invoices"

        result = await tool_node(state, client=mock_client)

        self.assertIsNone(result["tool_result"])
        self.assertEqual(result["verification_status"], "ERROR")
        self.assertIn("Sesi token JWT kadaluarsa", result["error_message"])

    async def test_permission_error_403_gracefully_captured(self):
        """HTTP 403 unauthorized company access is captured gracefully."""
        mock_client = MagicMock(spec=DjangoCoreClient)
        mock_client.fetch_vendor_invoices = AsyncMock(
            side_effect=DjangoPermissionError("User tidak memiliki akses ke company 3 (HTTP 403)")
        )

        state = create_initial_state("Cek tagihan vendor di company 3")
        state["detected_tool"] = "fetch_vendor_invoices"

        result = await tool_node(state, client=mock_client)

        self.assertIsNone(result["tool_result"])
        self.assertEqual(result["verification_status"], "ERROR")
        self.assertIn("User tidak memiliki akses ke company 3", result["error_message"])

    async def test_server_error_500_gracefully_captured(self):
        """HTTP 500 server error is captured gracefully."""
        mock_client = MagicMock(spec=DjangoCoreClient)
        mock_client.fetch_vendor_invoices = AsyncMock(
            side_effect=DjangoServerError("Django Core Internal Server Error (HTTP 500)")
        )

        state = create_initial_state("Cek tagihan")
        state["detected_tool"] = "fetch_vendor_invoices"

        result = await tool_node(state, client=mock_client)

        self.assertIsNone(result["tool_result"])
        self.assertEqual(result["verification_status"], "ERROR")
        self.assertIn("Internal Server Error", result["error_message"])

    async def test_network_connection_error_gracefully_captured(self):
        """Network drops / timeouts are captured gracefully."""
        mock_client = MagicMock(spec=DjangoCoreClient)
        mock_client.fetch_vendor_invoices = AsyncMock(
            side_effect=DjangoCoreClientError("Connection timed out to Django Core")
        )

        state = create_initial_state("Cek tagihan")
        state["detected_tool"] = "fetch_vendor_invoices"

        result = await tool_node(state, client=mock_client)

        self.assertIsNone(result["tool_result"])
        self.assertEqual(result["verification_status"], "ERROR")
        self.assertIn("Connection timed out", result["error_message"])


class TestDynamicToolDispatching(unittest.IsolatedAsyncioTestCase):
    """Test suite validating dynamic dispatcher across all core ERP tools."""

    async def test_dispatch_to_account_balance(self):
        """Routes to fetch_account_balance when detected_tool is get_account_balance."""
        mock_client = MagicMock(spec=DjangoCoreClient)
        mock_client.fetch_account_balance = AsyncMock(return_value={"accounts": [], "net_balance": 100000.0})

        state = create_initial_state("Cek saldo kas")
        state["detected_tool"] = "get_account_balance"
        state["extracted_params"] = {"account_code": "111100"}

        result = await tool_node(state, client=mock_client)

        mock_client.fetch_account_balance.assert_called_once()
        self.assertEqual(result["verification_status"], "VALID")
        self.assertEqual(result["tool_result"]["net_balance"], 100000.0)

    async def test_dispatch_to_stock_level(self):
        """Routes to fetch_stock_level when detected_tool is get_stock_level."""
        mock_client = MagicMock(spec=DjangoCoreClient)
        mock_client.fetch_stock_level = AsyncMock(return_value={"product_id": 10, "qty_on_hand": 50.0})

        state = create_initial_state("Cek stok tinta")
        state["detected_tool"] = "get_stock_level"
        state["extracted_params"] = {"product_id": 10}

        result = await tool_node(state, client=mock_client)

        mock_client.fetch_stock_level.assert_called_once()
        self.assertEqual(result["verification_status"], "VALID")
        self.assertEqual(result["tool_result"]["qty_on_hand"], 50.0)

    async def test_dispatch_to_product_info(self):
        """Routes to fetch_product_info when detected_tool is get_product_info."""
        mock_client = MagicMock(spec=DjangoCoreClient)
        mock_client.fetch_product_info = AsyncMock(return_value={"products": [{"id": 5, "name": "Kertas"}]})

        state = create_initial_state("Cari produk kertas")
        state["detected_tool"] = "get_product_info"
        state["extracted_params"] = {"search": "kertas"}

        result = await tool_node(state, client=mock_client)

        mock_client.fetch_product_info.assert_called_once()
        self.assertEqual(result["verification_status"], "VALID")
        self.assertEqual(len(result["tool_result"]["products"]), 1)

    async def test_dispatch_to_get_locations(self):
        """Routes to fetch_locations when detected_tool is get_locations."""
        mock_client = MagicMock(spec=DjangoCoreClient)
        mock_client.fetch_locations = AsyncMock(return_value={"total_found": 1, "locations": [{"id": 2, "name": "Main Store"}]})

        state = create_initial_state("Daftar gudang")
        state["detected_tool"] = "get_locations"
        state["extracted_params"] = {"search": "main"}

        result = await tool_node(state, client=mock_client)

        mock_client.fetch_locations.assert_called_once()
        self.assertEqual(result["verification_status"], "VALID")
        self.assertEqual(result["tool_result"]["total_found"], 1)

    async def test_sequential_location_resolution_before_stock_level(self):
        """Sequential 2-step execution: resolves location_id via fetch_locations before fetch_stock_level."""
        mock_client = MagicMock(spec=DjangoCoreClient)
        # Step 1: fetch_locations resolves 'mainstore' to id 2
        mock_client.fetch_locations = AsyncMock(
            return_value={"locations": [{"id": 2, "name": "Main Store"}]}
        )
        # Step 2: fetch_stock_level executed with location_id 2
        mock_client.fetch_stock_level = AsyncMock(
            return_value={"product_id": 10, "code": "AAEO-NPKG", "qty_on_hand": 1.05, "company_id": 3}
        )

        state = create_initial_state("kalau produk itu di mainstore berapa stoknya")
        state["company_id"] = 3
        state["detected_tool"] = "get_stock_level"
        state["extracted_params"] = {"code": "AAEO-NPKG", "location_name": "mainstore"}

        result = await tool_node(state, client=mock_client)

        # Assert Step 1 ran
        mock_client.fetch_locations.assert_called_once()
        loc_call_kwargs = mock_client.fetch_locations.call_args[1]
        self.assertEqual(loc_call_kwargs["params"]["search"], "mainstore")

        # Assert Step 2 ran with resolved location_id
        mock_client.fetch_stock_level.assert_called_once()
        stock_call_kwargs = mock_client.fetch_stock_level.call_args[1]
        self.assertEqual(stock_call_kwargs["params"]["location_id"], 2)
        self.assertEqual(stock_call_kwargs["params"]["code"], "AAEO-NPKG")

        # Result checks
        self.assertEqual(result["verification_status"], "VALID")
        self.assertEqual(result["tool_result"]["location_name"], "Main Store")


class TestSynchronousWrapperAndPipelineChain(unittest.TestCase):
    """Test suite for tool_node_sync and full LangGraph multi-node pipeline."""

    def test_tool_node_sync_execution(self):
        """tool_node_sync safely wraps async tool_node and preserves calculations."""
        mock_payload = {
            "entries": [{"id": 1, "vendor_name": "PT Sumber Makmur"}],
            "calculations": {"grand_total": 5000000.0},
        }
        mock_client = MagicMock(spec=DjangoCoreClient)
        mock_client.fetch_vendor_invoices = AsyncMock(return_value=mock_payload)

        state = create_initial_state("Cek tagihan vendor PT Sumber Makmur")
        state["detected_tool"] = "fetch_vendor_invoices"
        state["extracted_params"] = {"vendor_name": "PT Sumber Makmur"}

        result = tool_node_sync(state, client=mock_client)

        self.assertEqual(result["verification_status"], "VALID")
        self.assertIn("calculations", result["tool_result"])
        self.assertEqual(result["tool_result"]["calculations"]["grand_total"], 5000000.0)

    def test_three_node_pipeline_chain(self):
        """
        Integrates router_node -> extractor_node -> tool_node in StateGraph
        and executes query 'Cek tagihan vendor PT Sumber Makmur bulan ini'.
        """
        anchor_date = date(2026, 11, 4)
        mock_payload = {
            "total_count": 1,
            "entries": [
                {
                    "id": 99,
                    "number": "BILL/2026/099",
                    "vendor_name": "PT Sumber Makmur",
                    "total_debit": 12000000.0,
                }
            ],
            "calculations": {
                "grand_total": 12000000.0,
                "total_unpaid": 12000000.0,
            },
        }
        mock_client = MagicMock(spec=DjangoCoreClient)
        mock_client.fetch_vendor_invoices = AsyncMock(return_value=mock_payload)

        def node_router(s: AgentState) -> dict:
            return router_node(s)

        def node_extractor(s: AgentState) -> dict:
            return extractor_node(s, ref_date=anchor_date)

        def node_tool(s: AgentState) -> dict:
            return tool_node_sync(s, client=mock_client)

        builder = StateGraph(AgentState)
        builder.add_node("router", node_router)
        builder.add_node("extractor", node_extractor)
        builder.add_node("tool", node_tool)

        builder.add_edge(START, "router")
        builder.add_edge("router", "extractor")
        builder.add_edge("extractor", "tool")
        builder.add_edge("tool", END)

        workflow = builder.compile()

        initial_state = create_initial_state("Cek tagihan vendor PT Sumber Makmur bulan ini")
        final_state = workflow.invoke(initial_state)

        # 1. Router verification
        self.assertEqual(final_state["detected_domain"], "accounting")
        self.assertEqual(final_state["detected_tool"], "fetch_vendor_invoices")

        # 2. Extractor verification
        self.assertEqual(final_state["extracted_params"]["vendor_name"], "PT Sumber Makmur")
        self.assertEqual(final_state["extracted_params"]["date_from"], "2026-11-01")
        self.assertEqual(final_state["extracted_params"]["date_to"], "2026-11-30")

        # 3. Tool execution verification (DoD)
        self.assertEqual(final_state["verification_status"], "VALID")
        self.assertIsNotNone(final_state["tool_result"])
        self.assertIn("calculations", final_state["tool_result"])
        self.assertEqual(final_state["tool_result"]["calculations"]["grand_total"], 12000000.0)


class TestExecuteSingleToolDirect(unittest.IsolatedAsyncioTestCase):
    """Test suite validating _execute_single_tool uniform error tuple handling."""

    async def test_execute_single_tool_unregistered_tool(self):
        """Unregistered tool returns (None, error_str)."""
        mock_client = MagicMock(spec=DjangoCoreClient)
        res, err = await _execute_single_tool(
            tool_name="unregistered_tool",
            params={},
            erp_client=mock_client,
            user_token=None,
            default_company_id=1,
            dispatch_map={},
        )
        self.assertIsNone(res)
        self.assertIn("not registered in dispatch map", err)

    async def test_execute_single_tool_catches_django_client_error(self):
        """DjangoCoreClientError is caught and returned as (None, error_str)."""
        mock_handler = AsyncMock(side_effect=DjangoCoreClientError("Custom ERP 500 error", status_code=500))
        dispatch_map = {"my_tool": mock_handler}
        mock_client = MagicMock(spec=DjangoCoreClient)

        res, err = await _execute_single_tool(
            tool_name="my_tool",
            params={"search": "abc"},
            erp_client=mock_client,
            user_token=None,
            default_company_id=1,
            dispatch_map=dispatch_map,
        )
        self.assertIsNone(res)
        self.assertEqual(err, "Custom ERP 500 error")

    async def test_execute_single_tool_catches_unexpected_exception(self):
        """Unexpected exception is caught and returned as (None, error_str)."""
        mock_handler = AsyncMock(side_effect=ValueError("Simulated bad value"))
        dispatch_map = {"my_tool": mock_handler}
        mock_client = MagicMock(spec=DjangoCoreClient)

        res, err = await _execute_single_tool(
            tool_name="my_tool",
            params={},
            erp_client=mock_client,
            user_token=None,
            default_company_id=1,
            dispatch_map=dispatch_map,
        )
        self.assertIsNone(res)
        self.assertIn("Unexpected execution error", err)
        self.assertIn("Simulated bad value", err)


if __name__ == "__main__":
    unittest.main()
