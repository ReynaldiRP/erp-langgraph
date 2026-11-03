# FastPrint ERP — LangGraph Starter Kit

Starter kit integrasi agen AI (LangGraph) dengan FastPrint ERP.

## 📁 Struktur Berkas

- [`client.py`](file:///C:/laragon/www/ERP/integrations/langgraph/client.py): HTTP Client untuk komunikasi dengan `/ai/tools/` ERP via JWT Bearer Token.
- [`models.py`](file:///C:/laragon/www/ERP/integrations/langgraph/models.py): Factory ChatModel yang mendukung OpenRouter (free tier/flexible), Google Gemini, OpenAI, dan Ollama.
- [`tools_static.py`](file:///C:/laragon/www/ERP/integrations/langgraph/tools_static.py): Typed LangChain `@tool` wrappers untuk 4 tool pilot (accounting & inventory).
- [`tools_dynamic.py`](file:///C:/laragon/www/ERP/integrations/langgraph/tools_dynamic.py): Auto-discovery loader yang membaca schema dari endpoint `GET /ai/tools/`.
- [`prompts/erp_system_prompt.md`](file:///C:/laragon/www/ERP/integrations/langgraph/prompts/erp_system_prompt.md): System prompt & context domain ERP (multi-company rules, COA, stock logic, write safety).
- [`graph.py`](file:///C:/laragon/www/ERP/integrations/langgraph/graph.py): StateGraph ReAct agent dengan memori percakapan (`MemorySaver`) dan proteksi HITL.
- [`main.py`](file:///C:/laragon/www/ERP/integrations/langgraph/main.py): CLI interactive terminal runner untuk menguji agent.
- [`docs/LANGGRAPH_INTEGRATION.md`](file:///C:/laragon/www/ERP/integrations/langgraph/docs/LANGGRAPH_INTEGRATION.md): Dokumentasi teknis lengkap arsitektur, API schema, dan panduan integrasi.

## 🚀 Quick Start

1. Install dependensi:
   ```bash
   pip install -r requirements.txt
   ```
2. Salin dan sesuaikan konfigurasi env:
   ```bash
   cp .env.example .env
   ```
3. Jalankan interactive CLI:
   ```bash
   python main.py
   ```
