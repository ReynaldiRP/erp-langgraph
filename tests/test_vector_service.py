"""
Unit tests for VectorService (Dedicated PGVector + Google Gemini Embedding).
Tests embedding generation, query retrieval, text chunking, and fallback mechanisms.
"""
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.config import settings
from app.services.vector_service import VectorService
from scripts.ingest_knowledge import split_text_into_chunks


class TestVectorService(unittest.IsolatedAsyncioTestCase):
    """Test suite for VectorService with Gemini embeddings and PGVector mock."""

    def setUp(self):
        self.service = VectorService(
            db_url="postgresql://mock_user:mock_pass@localhost:5432/mock_vdb",
            gemini_api_key="mock_gemini_key",
            embedding_model="gemini-embedding-001",
        )

    def test_text_chunking(self):
        text = "Paragraf satu berisi kalimat.\n\nParagraf dua berisi teks penjelasan.\n\nParagraf tiga penutup."
        chunks = split_text_into_chunks(text, max_chunk_words=10, overlap_words=2)
        self.assertTrue(len(chunks) >= 1)
        self.assertIn("Paragraf", chunks[0])

    @patch("app.services.vector_service.VectorService._get_genai_client")
    async def test_get_embedding_gemini(self, mock_get_client):
        # Mock Google GenAI client
        mock_client = MagicMock()
        mock_embedding = MagicMock()
        mock_embedding.values = [0.01] * 768  # 768 dimensions
        mock_response = MagicMock()
        mock_response.embeddings = [mock_embedding]

        mock_client.models.embed_content.return_value = mock_response
        mock_get_client.return_value = mock_client

        vector = await self.service.get_embedding("SOP retur barang cetak")
        self.assertIsNotNone(vector)
        self.assertEqual(len(vector), 768)
        self.assertEqual(vector[0], 0.01)

    async def test_get_embedding_empty_text(self):
        vector = await self.service.get_embedding("   ")
        self.assertIsNone(vector)

    @patch("app.services.vector_service.VectorService.get_embedding")
    @patch("asyncpg.connect")
    async def test_search_knowledge_success(self, mock_asyncpg_connect, mock_get_embedding):
        # 768-dim mock vector
        mock_get_embedding.return_value = [0.05] * 768

        # Mock asyncpg connection and rows
        mock_conn = AsyncMock()
        mock_row = {
            "id": 1,
            "title": "SOP Retur Barang v1.2",
            "category": "sop",
            "content": "Pengajuan maksimal 3x24 jam.",
            "similarity_score": 0.885,
            "metadata": '{"source_file": "sop_retur.md"}',
        }
        mock_conn.fetch.return_value = [mock_row]
        mock_asyncpg_connect.return_value = mock_conn

        results = await self.service.search_knowledge(
            query="Bagaimana SOP retur barang?",
            company_id=1,
            top_k=3,
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "SOP Retur Barang v1.2")
        self.assertEqual(results[0]["similarity_score"], 0.885)
        self.assertIn("source_file", results[0]["metadata"])

    async def test_search_knowledge_no_db_url(self):
        service_no_db = VectorService(db_url=None, gemini_api_key="mock_key")
        with patch.object(service_no_db, "get_embedding", return_value=[0.1] * 768):
            results = await service_no_db.search_knowledge("Query")
            self.assertEqual(results, [])


if __name__ == "__main__":
    unittest.main()
