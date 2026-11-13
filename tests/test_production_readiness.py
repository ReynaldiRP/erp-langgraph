"""
Verification tests for production readiness improvements (D1 & D2).
"""
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient
import httpx
from langgraph.checkpoint.memory import MemorySaver

from app.core.config import settings
from app.graph.workflow import (
    arun_workflow,
    astream_workflow_steps,
    create_workflow_graph,
)
from app.main import app
from app.services.django_client import DjangoAuthError, DjangoCoreClient


class TestD1SessionStateAndBoundedCheckpointer(unittest.IsolatedAsyncioTestCase):
    """Verify D1: ephemeral stateless thread isolation, history hydration, and LRU eviction."""

    def test_max_checkpoint_threads_default_config(self):
        """settings.MAX_CHECKPOINT_THREADS defaults to 500."""
        self.assertEqual(settings.MAX_CHECKPOINT_THREADS, 500)

    async def test_stateless_requests_do_not_leak_company_id_or_messages(self):
        """
        Requests without thread_id use unique ephemeral thread IDs, clean up all RAM state
        afterwards, do not leak company_id/messages across requests, and always hydrate history.
        """
        checkpointer = MemorySaver()
        graph = create_workflow_graph(checkpointer=checkpointer)

        # Turn 1 (stateless, company_id=6)
        res_1 = await arun_workflow(
            user_query="Halo asisten",
            company_id=6,
            thread_id=None,
            graph=graph,
        )
        self.assertEqual(res_1.get("company_id"), 6)
        self.assertEqual(len(checkpointer.storage), 0)
        self.assertEqual(len(checkpointer.writes), 0)
        self.assertEqual(len(checkpointer.blobs), 0)

        # Turn 2 (stateless, no company_id, with history -> must hydrate history and default company_id=1)
        mock_client = MagicMock(spec=DjangoCoreClient)
        mock_client.aclose = AsyncMock()
        mock_client.fetch_locations = AsyncMock(return_value={"locations": []})
        mock_client.fetch_product_info = AsyncMock(
            return_value={
                "products": [
                    {
                        "id": 101,
                        "code": "SKU-EPHEMERAL-01",
                        "name": "Kertas Ephemeral",
                        "qty_available": 15.0,
                    }
                ]
            }
        )
        mock_client.fetch_stock_level = AsyncMock(
            return_value=[
                {
                    "code": "SKU-EPHEMERAL-01",
                    "name": "Kertas Ephemeral",
                    "company_id": 1,
                    "company_name": "FastPrint",
                    "location_name": "Gudang Utama",
                    "qty_on_hand": 15.0,
                }
            ]
        )

        history_payload = [
            {"role": "user", "content": "cek stok produk SKU-EPHEMERAL-01"},
            {"role": "assistant", "content": "Stok SKU-EPHEMERAL-01 belum ditemukan."},
        ]

        with patch("app.graph.nodes.tool_node.DjangoCoreClient", return_value=mock_client):
            res_2 = await arun_workflow(
                user_query="coba cek lagi",
                company_id=None,
                thread_id=None,
                history=history_payload,
                graph=graph,
            )

        self.assertEqual(res_2.get("company_id"), settings.DEFAULT_COMPANY_ID)
        contents = [getattr(m, "content", "") for m in res_2.get("messages", [])]
        self.assertTrue(any("SKU-EPHEMERAL-01" in c for c in contents))
        self.assertFalse(any("Halo asisten" in c for c in contents))
        self.assertEqual(len(checkpointer.storage), 0)
        self.assertEqual(len(checkpointer.writes), 0)
        self.assertEqual(len(checkpointer.blobs), 0)

    async def test_stateless_stream_cleans_up_ephemeral_thread_on_completion_and_early_close(self):
        """
        astream_workflow_steps with thread_id=None leaves 0 threads in MemorySaver,
        both on full stream completion and when closed early.
        """
        checkpointer = MemorySaver()
        graph = create_workflow_graph(checkpointer=checkpointer)

        # 1. Full completion
        steps = []
        async for step in astream_workflow_steps(
            user_query="Halo asisten",
            company_id=4,
            thread_id="",
            graph=graph,
        ):
            steps.append(step[0])
        self.assertIn("synthesizer", steps)
        self.assertEqual(len(checkpointer.storage), 0)

        # 2. Early generator close
        stream = astream_workflow_steps(
            user_query="Halo asisten",
            company_id=5,
            thread_id=None,
            graph=graph,
        )
        first_step = await stream.__anext__()
        self.assertIsNotNone(first_step)
        await stream.aclose()
        self.assertEqual(len(checkpointer.storage), 0)
        self.assertEqual(len(checkpointer.writes), 0)
        self.assertEqual(len(checkpointer.blobs), 0)

    async def test_bounded_checkpointer_lru_eviction(self):
        """
        MemorySaver never exceeds settings.MAX_CHECKPOINT_THREADS and evicts
        the least-recently-used thread across both arun_workflow and astream_workflow_steps.
        """
        checkpointer = MemorySaver()
        graph = create_workflow_graph(checkpointer=checkpointer)

        with patch.object(settings, "MAX_CHECKPOINT_THREADS", 3):
            await arun_workflow(user_query="Halo", thread_id="thread_a", graph=graph)
            await arun_workflow(user_query="Halo", thread_id="thread_b", graph=graph)
            await arun_workflow(user_query="Halo", thread_id="thread_c", graph=graph)
            self.assertEqual(set(checkpointer.storage.keys()), {"thread_a", "thread_b", "thread_c"})

            # Re-access thread_a so thread_b becomes the least-recently-used
            await arun_workflow(user_query="Apa kabar?", thread_id="thread_a", graph=graph)

            # Adding thread_d must evict thread_b
            await arun_workflow(user_query="Halo", thread_id="thread_d", graph=graph)
            self.assertEqual(len(checkpointer.storage), 3)
            self.assertEqual(set(checkpointer.storage.keys()), {"thread_a", "thread_c", "thread_d"})
            self.assertFalse(any(k[0] == "thread_b" for k in checkpointer.writes))
            self.assertFalse(any(k[0] == "thread_b" for k in checkpointer.blobs))

            # Streaming thread_e must evict thread_c (now oldest)
            async for _ in astream_workflow_steps(user_query="Halo", thread_id="thread_e", graph=graph):
                pass
            self.assertEqual(len(checkpointer.storage), 3)
            self.assertEqual(set(checkpointer.storage.keys()), {"thread_a", "thread_d", "thread_e"})
            self.assertFalse(any(k[0] == "thread_c" for k in checkpointer.writes))
            self.assertFalse(any(k[0] == "thread_c" for k in checkpointer.blobs))


class TestD2ApiAuthenticationPolicy(unittest.TestCase):
    """Verify D2: strict user JWT enforcement, Bearer header extraction, and service key guard."""

    def setUp(self):
        self.client = TestClient(app)

    def test_auth_policy_config_defaults(self):
        """REQUIRE_USER_TOKEN and REQUIRE_SERVICE_KEY default to False."""
        self.assertFalse(settings.REQUIRE_USER_TOKEN)
        self.assertFalse(settings.REQUIRE_SERVICE_KEY)

    @patch("app.main.arun_workflow", new_callable=AsyncMock)
    def test_production_strict_user_token_and_bearer_header(self, mock_workflow):
        """
        In production (or REQUIRE_USER_TOKEN=True), missing/blank token returns HTTP 401
        and Authorization: Bearer <jwt> or body user_token succeeds.
        """
        mock_workflow.return_value = {
            "final_answer": "OK",
            "detected_domain": "general",
            "verification_status": "VALID",
            "error_message": None,
        }

        with patch.object(settings, "APP_ENV", "production"):
            # Missing both body token and Bearer header -> 401
            resp_no_auth = self.client.post("/api/v1/chat", json={"query": "Cek saldo kas"})
            self.assertEqual(resp_no_auth.status_code, 401)
            mock_workflow.assert_not_awaited()

            # Blank user_token + non-Bearer Authorization -> 401
            resp_bad_scheme = self.client.post(
                "/api/v1/chat",
                json={"query": "Cek saldo kas", "user_token": "   "},
                headers={"Authorization": "Basic abc123"},
            )
            self.assertEqual(resp_bad_scheme.status_code, 401)
            mock_workflow.assert_not_awaited()

            # Valid Authorization: Bearer <jwt> -> 200 and forwards header token
            resp_bearer = self.client.post(
                "/api/v1/chat",
                json={"query": "Cek saldo kas"},
                headers={"Authorization": "Bearer jwt_from_bearer_header"},
            )
            self.assertEqual(resp_bearer.status_code, 200)
            self.assertEqual(mock_workflow.await_args.kwargs.get("user_token"), "jwt_from_bearer_header")

            # Body user_token takes precedence over Bearer header when both present
            resp_body = self.client.post(
                "/api/v1/chat",
                json={"query": "Cek saldo kas", "user_token": "jwt_from_body"},
                headers={"Authorization": "Bearer jwt_from_bearer_header"},
            )
            self.assertEqual(resp_body.status_code, 200)
            self.assertEqual(mock_workflow.await_args.kwargs.get("user_token"), "jwt_from_body")

        # REQUIRE_USER_TOKEN=True also enforces 401 even in development mode
        mock_workflow.reset_mock()
        with patch.object(settings, "APP_ENV", "development"), \
             patch.object(settings, "REQUIRE_USER_TOKEN", True):
            resp_flag = self.client.post("/api/v1/chat", json={"query": "Cek saldo kas"})
            self.assertEqual(resp_flag.status_code, 401)
            mock_workflow.assert_not_awaited()

    @patch("app.main.arun_workflow", new_callable=AsyncMock)
    def test_production_masks_internal_500_details(self, mock_workflow):
        """Production mode does not leak internal exception details on HTTP 500."""
        mock_workflow.side_effect = RuntimeError("postgresql://secret-internal-host/db failed")
        with patch.object(settings, "APP_ENV", "production"):
            resp = self.client.post(
                "/api/v1/chat",
                json={"query": "Cek saldo kas", "user_token": "valid_jwt"},
            )
        self.assertEqual(resp.status_code, 500)
        detail = resp.json().get("detail", "")
        self.assertEqual(detail, "Chat workflow failed due to an internal server error.")
        self.assertNotIn("secret-internal-host", detail)

    @patch("app.main.arun_workflow", new_callable=AsyncMock)
    def test_service_key_enforcement_when_enabled(self, mock_workflow):
        """When REQUIRE_SERVICE_KEY=True, X-Internal-Service-Key must match ERP_API_TOKEN."""
        mock_workflow.return_value = {
            "final_answer": "OK",
            "detected_domain": "general",
            "verification_status": "VALID",
            "error_message": None,
        }

        with patch.object(settings, "REQUIRE_SERVICE_KEY", True), \
             patch.object(settings, "ERP_API_TOKEN", "internal-secret-123"):
            resp_missing = self.client.post("/api/v1/chat", json={"query": "Halo"})
            self.assertEqual(resp_missing.status_code, 403)

            resp_wrong = self.client.post(
                "/api/v1/chat",
                json={"query": "Halo"},
                headers={"X-Internal-Service-Key": "wrong-key"},
            )
            self.assertEqual(resp_wrong.status_code, 403)

            resp_ok = self.client.post(
                "/api/v1/chat",
                json={"query": "Halo"},
                headers={"X-Internal-Service-Key": "internal-secret-123"},
            )
            self.assertEqual(resp_ok.status_code, 200)


class TestD2DjangoClientUserTokenGuard(unittest.IsolatedAsyncioTestCase):
    """Verify D2: DjangoCoreClient(require_user_token=True) blocks fallback to admin/system auth."""

    async def test_require_user_token_rejects_missing_token_without_fallback(self):
        """Missing or whitespace user_token raises DjangoAuthError(401) and never calls login()."""
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        client = DjangoCoreClient(
            base_url="http://erp.local",
            token="system_admin_token",
            username="admin",
            password="secret",
            client=mock_http,
            require_user_token=True,
        )
        client.login = AsyncMock()

        with self.assertRaises(DjangoAuthError) as ctx:
            await client.fetch_account_balance(params={}, user_token=None, company_id=1)
        self.assertEqual(ctx.exception.status_code, 401)
        client.login.assert_not_awaited()
        mock_http.request.assert_not_called()

        with self.assertRaises(DjangoAuthError):
            await client.fetch_account_balance(params={}, user_token="   ", company_id=1)
        client.login.assert_not_awaited()
        mock_http.request.assert_not_called()

    async def test_require_user_token_forwards_valid_jwt_and_never_falls_back_on_401(self):
        """Valid user_token is forwarded; 401 from Django does NOT trigger system login()."""
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 401
        mock_resp.text = "Token expired"
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.request = AsyncMock(return_value=mock_resp)

        client = DjangoCoreClient(
            base_url="http://erp.local",
            token="system_admin_token",
            username="admin",
            password="secret",
            client=mock_http,
            require_user_token=True,
        )
        client.login = AsyncMock()

        with self.assertRaises(DjangoAuthError):
            await client.fetch_account_balance(params={}, user_token="user_jwt_999", company_id=1)

        client.login.assert_not_awaited()
        called_headers = mock_http.request.call_args.kwargs["headers"]
        self.assertEqual(called_headers["Authorization"], "Bearer user_jwt_999")


if __name__ == "__main__":
    unittest.main()
