# FastPrint ERP & LangGraph Integration Guide

Panduan teknis lengkap integrasi antara FastPrint ERP (`ai_tools` layer) dengan agen otonom berbasis LangGraph / LangChain.

---

## 1. Arsitektur Sistem

```
┌──────────────────────────────────────────────┐
│            LangGraph Agent Service           │
│  (models.py, graph.py, MemorySaver, Prompt)  │
└──────────────────────┬───────────────────────┘
                       │ HTTP REST (JSON)
                       │ Header: Authorization: Bearer <JWT>
                       │ Header: X-AI-Source: LangGraph
                       ▼
┌──────────────────────────────────────────────┐
│               FastPrint ERP                  │
│                                              │
│  ├── CheckSessionMiddleware (JWT Validation) │
│  ├── SystemErrorLogMiddleware (Audit Trail)  │
│  └── /ai/tools/ (CSRF-Exempt Dispatcher)     │
│       ├── Domain Resolver                    │
│       ├── Company Access Validator           │
│       ├── Write Confirmation Validator       │
│       └── Domain Registries:                 │
│            ├── accounting (Balance, Journal) │
│            └── inventory  (Stock, Product)   │
└──────────────────────────────────────────────┘
```

---

## 2. Prasyarat di Sisi ERP

Agar endpoint `/ai/tools/` aktif di ERP Django:
1. Pastikan variabel berikut ada di `.env` utama ERP:
   ```env
   AI_ENABLED=True
   ```
2. Endpoint routing otomatis dipasang di:
   - Discovery: `GET /ai/tools/`
   - Eksekusi: `POST /ai/tools/<domain>/<tool_name>/`

---

## 3. Autentikasi & Scoping Perusahaan

### 3.1 Bearer Token
Setiap request ke `/ai/tools/` wajib menyertakan token JWT:
```http
Authorization: Bearer <access_token>
Content-Type: application/json
X-AI-Source: LangGraph
```

### 3.2 Alur Login Otomatis
Jika token belum ada atau expired, `ERPClient` secara otomatis memanggil:
- `POST /external_api/get_token/`
- Header `key`: MD5 hash dari `erp_master_nexus-YYYY-MM-DD-HH:MM`
- Payload: `{"username": "...", "password": "..."}`
- Response: `{"access": "<jwt_access_token>", "refresh": "<jwt_refresh_token>"}`

### 3.3 Multi-Company Scoping
ERP FastPrint mengisolasi data per perusahaan (`company_id`).
- Setiap eksekusi tool wajib mengirimkan `company_id`.
- Backend memvalidasi apakah user yang login memiliki hak akses ke `company_id` tersebut.
- Jika user tidak memiliki akses, server mengembalikan HTTP 403 `PERMISSION_DENIED`.

---

## 4. Katalog Tools yang Telah Diimplementasikan

Saat ini terdapat 4 tool pilot yang siap digunakan:

### 4.1 Domain Accounting

#### 1. `accounting.get_account_balance`
- **Tipe**: Read-Only (`is_write: false`)
- **Deskripsi**: Menghitung total debit, kredit, dan saldo bersih chart of account.
- **Parameter**:
  | Field | Tipe | Wajib | Keterangan |
  |---|---|---|---|
  | `account_id` | integer | Tidak | ID spesifik akun |
  | `account_code` | string | Tidak | Kode akun (misal: `"111100"`) |
  | `date_from` | string (YYYY-MM-DD) | Tidak | Batas awal tanggal jurnal |
  | `date_to` | string (YYYY-MM-DD) | Tidak | Batas akhir tanggal jurnal |

#### 2. `accounting.get_journal_entries`
- **Tipe**: Read-Only (`is_write: false`)
- **Deskripsi**: Mengambil data jurnal umum beserta baris debit/kredit (items).
- **Parameter**:
  | Field | Tipe | Wajib | Keterangan |
  |---|---|---|---|
  | `journal_id` | integer | Tidak | ID buku jurnal |
  | `entry_number` | string | Tidak | Nomor transaksi jurnal (misal: `"JE/2026/..."`) |
  | `state` | string | Tidak | `"draft"`, `"posted"`, atau `"cancelled"` |
  | `date_from` | string (YYYY-MM-DD) | Tidak | Dari tanggal |
  | `date_to` | string (YYYY-MM-DD) | Tidak | Sampai tanggal |
  | `account_id` | integer | Tidak | Filter jurnal yang memiliki mutasi akun ini |
  | `limit` | integer | Tidak | Limit hasil (default: 20, maks: 100) |
  | `offset` | integer | Tidak | Offset pagination (default: 0) |

---

### 4.2 Domain Inventory

#### 3. `inventory.get_stock_level`
- **Tipe**: Read-Only (`is_write: false`)
- **Deskripsi**: Memeriksa kuantitas fisik (`quantity`) dan stok tersedia (`available_quantity`) per gudang.
- **Parameter**:
  | Field | Tipe | Wajib | Keterangan |
  |---|---|---|---|
  | `product_id` | integer | Tidak | ID produk / varian |
  | `warehouse_id` | integer | Tidak | ID gudang spesifik |
  | `sku` | string | Tidak | SKU produk |
  | `barcode` | string | Tidak | Barcode produk |
  | `limit` | integer | Tidak | Limit pagination (default: 20, maks: 100) |
  | `offset` | integer | Tidak | Offset pagination (default: 0) |

#### 4. `inventory.get_product_info`
- **Tipe**: Read-Only (`is_write: false`)
- **Deskripsi**: Menampilkan master produk, varian, satuan (UoM), dan harga jual.
- **Parameter**:
  | Field | Tipe | Wajib | Keterangan |
  |---|---|---|---|
  | `product_id` | integer | Tidak | ID produk spesifik |
  | `query` | string | Tidak | Pencarian teks nama produk |
  | `sku` | string | Tidak | SKU produk |
  | `barcode` | string | Tidak | Barcode produk |
  | `include_variants` | boolean | Tidak | Apakah menampilkan detail varian (default: true) |
  | `limit` | integer | Tidak | Limit pagination (default: 20, maks: 50) |

---

## 5. Keamanan Operasi Tulis (Write Safety)

Pada tool yang mengubah database (`is_write: true`):
1. Payload wajib menyertakan `"confirm": true`.
2. Jika `"confirm": false` atau tidak disertakan, server melempar error `CONFIRMATION_REQUIRED` (HTTP 400).
3. Di LangGraph, aktifkan `ENABLE_HITL_INTERRUPT=true` untuk memicu interrupt sebelum tool dieksekusi agar user di terminal/web UI dapat menyetujui parameter terlebih dahulu.

---

## 6. Cara Menjalankan Starter Kit

### 6.1 Setup Virtual Environment
```bash
cd integrations/langgraph
python -m venv venv
# Windows:
.\venv\Scripts\activate
# Linux/Mac:
source venv/bin/activate
pip install -r requirements.txt
```

### 6.2 Konfigurasi `.env`
Salin template konfigurasi:
```bash
cp .env.example .env
```
Isi kredensial:
- Model gratisan (OpenRouter): Isi `OPENROUTER_API_KEY` dan pilih model `meta-llama/llama-3.3-70b-instruct:free`.
- Model Google Gemini: Set `MODEL_PROVIDER=gemini` dan isi `GEMINI_API_KEY`.
- Model OpenAI: Set `MODEL_PROVIDER=openai` dan isi `OPENAI_API_KEY`.

### 6.3 Menjalankan CLI Interaktif
```bash
python main.py
```

Contoh percakapan:
```text
User > Berapa saldo akun kas (111100) untuk company 1 dari 1 Januari 2026 sampai sekarang?
[Tool Invoked] accounting_get_account_balance({'account_code': '111100', 'company_id': 1, 'date_from': '2026-01-01'})

Assistant:
Berdasarkan data ERP FastPrint untuk Company ID 1 per 1 Januari 2026 hingga saat ini:
- Total Debit  : Rp 150.000.000,00
- Total Kredit : Rp 75.000.000,00
- Saldo Bersih : Rp 75.000.000,00 (Posisi Debit)
```

---

## 7. Cara Menambahkan Tool Baru di Django ERP

Saat menambahkan fitur AI baru di Django:
1. Buat class baru yang meng-extend `BaseTool` di `ai_tools/tools/<domain>/<tool_name>.py`.
2. Definisikan `domain`, `name`, `description`, `is_write`, dan `params_schema`.
3. Implementasikan method `validate_params(params)` dan `execute(user, company, params)`.
4. Daftarkan di dictionary registry domain (`ai_tools/tools/<domain>/registry.py`).
5. **Otomatis terbaca**: Jika LangGraph menggunakan `USE_DYNAMIC_TOOLS=true`, tool baru tersebut langsung dikenali oleh agent tanpa perlu merestart atau menulis ulang kode LangGraph!
