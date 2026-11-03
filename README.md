# FastPrint ERP — AI Orchestrator Service

Dedicated AI Orchestrator service berbasis **FastAPI** dan **LangGraph** untuk integrasi agen AI otonom dengan FastPrint ERP (`ai_tools` layer).

---

## 🏛️ Arsitektur Sistem

```mermaid
graph TD
    Client[Client / Frontend / External Service] -->|HTTP REST| API[FastAPI Orchestrator Service<br/>Port 8001]
    Terminal[Developer / Admin] -->|Terminal CLI| CLI[cli.py Runner]
    
    subgraph "erp-ai-orchestrator (app/)"
        API --> AppMain[app/main.py]
        CLI --> AgentGraph[app/graph/agent.py<br/>LangGraph ReAct]
        AppMain -.-> AgentGraph
        
        AgentGraph --> LLM[app/services/llm.py<br/>OpenRouter / OpenAI]
        AgentGraph --> Tools[app/tools/<br/>Static & Dynamic Tools]
        Tools --> ERPClient[app/services/erp_client.py<br/>HTTPX Client]
        
        Config[app/core/config.py<br/>Pydantic Settings] --> API
        Config --> ERPClient
        Config --> LLM
    end
    
    ERPClient -->|HTTP REST / JWT Bearer| ERP[FastPrint ERP Django<br/>Port 8000 /ai/tools/]
```

---

## 📁 Struktur Berkas

```text
erp-langgraph/
├── app/
│   ├── __init__.py
│   ├── main.py                 # Entry point service FastAPI (GET /health, etc.)
│   ├── core/
│   │   ├── __init__.py
│   │   └── config.py           # Konfigurasi terpusat berbasis pydantic-settings
│   ├── services/
│   │   ├── __init__.py
│   │   ├── erp_client.py       # HTTP Client (httpx) untuk komunikasi ke ERP /ai/tools/
│   │   └── llm.py              # LLM factory mendukung OpenRouter & OpenAI
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── static.py           # 4 pilot typed tools (Accounting & Inventory)
│   │   └── dynamic.py          # Dynamic tool loader dari GET /ai/tools/
│   └── graph/
│       ├── __init__.py
│       └── agent.py            # LangGraph ReAct StateGraph agent
├── cli.py                      # Interactive terminal runner untuk pengujian langsung
├── prompts/
│   └── erp_system_prompt.md    # System prompt context domain ERP
├── docs/
│   ├── AI_TOOLS_HANDOFF.md     # Spesifikasi handoff resmi
│   └── LANGGRAPH_INTEGRATION.md# Dokumentasi teknis integrasi
├── requirements.txt            # Dependensi Python terpadu
├── .env.example                # Template konfigurasi environment
└── README.md                   # Dokumentasi proyek
```

---

## ⚙️ Konfigurasi Environment (`.env`)

| Variabel | Tipe | Default | Keterangan |
|---|---|---|---|
| `APP_NAME` | string | `"erp-ai-orchestrator"` | Nama aplikasi service |
| `APP_ENV` | string | `"development"` | Mode environment (`development`/`production`) |
| `APP_HOST` | string | `"0.0.0.0"` | Host binding FastAPI |
| `APP_PORT` | integer | `8001` | Port service FastAPI (Port 8001 agar tidak bentrok dengan ERP Django di port 8000) |
| `LOG_LEVEL` | string | `"info"` | Logging verbosity |
| `ERP_BASE_URL` | string | `"http://127.0.0.1:8000"` | Base URL FastPrint ERP backend |
| `DEFAULT_COMPANY_ID` | integer | `1` | ID perusahaan default untuk multi-company scoping |
| `ERP_API_TOKEN` | string | `""` | Static JWT token (opsional, auto-login jika kosong) |
| `ERP_USERNAME` | string | `"admin"` | Username login ERP |
| `ERP_PASSWORD` | string | `""` | Password login ERP |
| `REQUEST_TIMEOUT_SEC` | integer | `15` | Timeout (detik) untuk HTTP client ERP dan request LLM (alias: `REQUEST_TIMEOUT`) |
| `MODEL_PROVIDER` | string | `"openrouter"` | Provider LLM aktif (`openrouter` atau `openai`) |
| `OPENROUTER_API_KEY` | string | `""` | API key OpenRouter |
| `OPENROUTER_MODEL` | string | `"meta-llama/llama-3.3-70b-instruct:free"` | Model target di OpenRouter |
| `OPENAI_API_KEY` | string | `""` | API key OpenAI |
| `OPENAI_MODEL` | string | `"gpt-4o-mini"` | Model target OpenAI |
| `USE_DYNAMIC_TOOLS` | boolean | `false` | `true` untuk fetch tools dari ERP saat runtime |
| `ENABLE_HITL_INTERRUPT` | boolean | `false` | `true` untuk jeda eksekusi tool write (Human-in-the-Loop) |

---

## 🚀 Quick Start

### 1. Inisialisasi Environment & Install Dependensi

Gunakan Python 3.11+:

```powershell
# Buat virtual environment
py -3.11 -m venv .venv

# Aktifkan virtual environment (PowerShell)
.venv\Scripts\Activate.ps1

# Install dependensi
pip install -r requirements.txt
```

### 2. Konfigurasi File `.env`

```powershell
cp .env.example .env
```
Sesuaikan `ERP_USERNAME`, `ERP_PASSWORD`, serta `OPENROUTER_API_KEY` / `OPENAI_API_KEY`.

### 3. Menjalankan Service FastAPI

Jalankan server menggunakan Uvicorn:

```powershell
uvicorn app.main:app --port 8001 --reload
```

#### Verifikasi Endpoint Health:
```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8001/health" -Method Get
```
Output:
```json
{"status": "healthy"}
```

#### Dokumentasi API Otomatis (Swagger UI):
Buka di browser:
- Swagger UI: [http://localhost:8001/docs](http://localhost:8001/docs)
- ReDoc: [http://localhost:8001/redoc](http://localhost:8001/redoc)

### 4. Menjalankan Terminal CLI Interaktif (Opsional)

Untuk pengujian agent secara langsung via terminal console:

```powershell
python cli.py
```
