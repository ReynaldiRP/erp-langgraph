# Architecture & Quality Audit Report: Multi-Tool & Cross-Module Execution

**Target Repository**: `erp-langgraph`  
**Scope**: Multi-Tool & Cross-Module Execution implementation (Phase 2 / Milestones 1–5)  
**Date**: 2026-11-07  

---

## What This Repo Does
This repository provides an autonomous LangGraph orchestrator that answers accounting and inventory queries for FastPrint ERP. It parses user questions, extracts parameters against Pydantic schemas, calls Django ERP endpoints concurrently, verifies the data, and formats Markdown responses with IDR currency tables.

**Load Assumed**: A FastAPI service (`uvicorn` port 8001) and interactive CLI serving 10–50 concurrent company staff querying accounting ledgers and warehouse inventory across multiple company IDs.

---

## Audit Findings

### Group 1: Must Fix

#### 1. Partial tool failure handled inconsistently depending on list order
- **Location**: `app/graph/nodes/verifier.py:L88-130`, `app/graph/nodes/tool_node.py:L154-166`
- **What this is**: When running multiple tools, `tool_node` records errors per tool, while `verifier_node` determines if the result is valid or empty.
- **Problem**: If tool A fails with an error and tool B succeeds, `tool_node` sets `tool_result` to tool A's result (`None`). `verifier_node` sees `tool_result is None` with an error message and aborts the entire turn as `ERROR`. If tool B were first in the list instead, `verifier_node` ignores tool A's error and logs that all tools succeeded.
- **Fix**: In `verifier_node`, check `multi_verification_status` directly instead of checking scalar `tool_result is None`, and mark the turn `VALID` whenever at least one tool succeeds with records.
- **If we skip it**: Users with dual-intent questions receive total system errors whenever the first planned tool encounters an issue, even if the other tool returned complete data.

#### 2. Duplicate tool names overwrite each other in multi-tool results
- **Location**: `app/graph/nodes/tool_node.py:L127-155`, `app/graph/nodes/synthesizer.py:L302-308`
- **What this is**: `multi_tool_results` stores tool execution outputs inside a dictionary keyed by tool name.
- **Problem**: If a query plans two calls for the same tool (such as checking stock for two different products via `get_stock_level`), the second call overwrites the first in `multi_tool_results[tool_name]`, and the synthesizer renders the second result twice.
- **Fix**: Key `multi_tool_results` by unique plan index or composite key (`f"{tool_name}_{idx}"`), or enforce strict tool de-duplication in `MultiRouterDecision`.
- **If we skip it**: Multi-product stock checks silently drop data for all items except the last one.

#### 3. `_calculate_records_count` defaults unexpected dicts to 1 record
- **Location**: `app/graph/nodes/verifier.py:L52-64`
- **What this is**: The verifier inspects raw ERP responses to count how many records were found.
- **Problem**: Any dictionary without known collection keys (`entries`, `accounts`, etc.) falls through to `return 1`. If an ERP endpoint returns an error payload with HTTP 200 (such as `{"detail": "Warehouse not found"}`), the verifier treats it as 1 valid record.
- **Fix**: Check for `"error"` or `"detail"` keys, and verify the presence of actual data attributes (e.g. `product_id`, `qty_on_hand`, `id`) before defaulting to 1.
- **If we skip it**: Error messages returned by upstream services get treated as successful data records and formatted into blank tables.

---

### Group 2: Should Fix

#### 4. Multi-word product names truncated in stock fallback extractor
- **Location**: `app/graph/nodes/extractor.py:L260-280`
- **What this is**: Fallback extractor for `get_stock_level` when LLM extraction is unavailable or offline.
- **Problem**: The regex `([A-Za-z0-9\-_\.]+)` stops on the first space, turning `"Art Paper"` into `"Art"` and `"Kertas Glossy A4"` into `"Kertas"`.
- **Fix**: Update the pattern to capture multi-word tokens up to the next delimiter or keyword: `r"(?:stok|barang|produk)\s+([A-Za-z0-9\-_\.\s]+?)(?:\s+(?:di|pada|gudang|lokasi)|$)"`.
- **If we skip it**: Offline or fallback stock lookups fail to find items with multi-word names.

#### 5. Trailing punctuation and emoji break vendor extraction
- **Location**: `app/graph/nodes/extractor.py:L185-215`
- **What this is**: Fallback extractor parsing vendor names from queries like `"Cek tagihan vendor PT Sumber Makmur?"`.
- **Problem**: The delimiter regex relies on `(?:\s+{VENDOR_DELIMITERS}|$)`. A trailing question mark (`?`) or emoji prevents matching `$`, returning an empty vendor name.
- **Fix**: Strip trailing punctuation `text.rstrip("?!. ")` before running regex extractors.
- **If we skip it**: Questions phrased with a question mark at the end fail vendor name extraction in fallback mode.

#### 6. Anaphoric clitic `-nya` does not trigger conversational history lookup
- **Location**: `app/graph/nodes/extractor.py:L270-285`
- **What this is**: Multi-turn extraction resolving prior entities when user says `"Berapa stoknya"`.
- **Problem**: History lookup checks for words like `"itu"` and `"tadi"`, but omits `-nya` and `stoknya`, leaving product code `None`.
- **Fix**: Include `has_anaphoric_reference(sub_query)` when deciding to inspect conversation history for stock extraction.
- **If we skip it**: Follow-up questions like `"Berapa stoknya?"` fail to look up the product discussed in the previous turn.

#### 7. Location fallback extractor captures `"gudang"` as location name
- **Location**: `app/graph/nodes/extractor.py:L320-345`
- **What this is**: Fallback extractor resolving warehouse location queries.
- **Problem**: In `"lokasi gudang utama"`, the regex matches `"gudang"` as the search term instead of `"utama"`.
- **Fix**: Strip leading stop words (`gudang`, `lokasi`, `warehouse`) from the matched group before returning `search`.
- **If we skip it**: Filtering by warehouse name searches for the word `"gudang"` rather than the actual warehouse label.

#### 8. Empty invoices in multi-tool output render an empty heading
- **Location**: `app/graph/nodes/synthesizer.py:L59-78`
- **What this is**: Synthesizer formatter for vendor invoices and journal entries.
- **Problem**: When an invoice query returns 0 entries, `entries` is empty, so table creation is skipped, printing only `### Laporan Transaksi Tagihan / Jurnal (0 Catatan Ditemukan)` with no explanatory body text.
- **Fix**: Add an explicit fallback message: `lines.append("Tidak ada transaksi tercatat pada periode ini.")` when `len(entries) == 0`.
- **If we skip it**: Users see an empty section header without explanation.

#### 9. `tool_node_sync` crashes if passed an active async client
- **Location**: `app/graph/nodes/tool_node.py:L237-241`
- **What this is**: Synchronous wrapper for executing `tool_node`.
- **Problem**: `ThreadPoolExecutor` runs `asyncio.run()` in a new thread. If the caller passed an existing `DjangoCoreClient`, its internal `httpx.AsyncClient` is bound to the outer loop and throws a cross-loop `RuntimeError`.
- **Fix**: When running inside an active loop, pass `client=None` to ensure the worker thread initializes its own client, or run `loop.run_until_complete`.
- **If we skip it**: Running synchronous tests or CLI helpers with an injected client crashes with event loop conflicts.

#### 10. `_execute_single_tool` mixes exceptions and error tuples
- **Location**: `app/graph/nodes/tool_node.py:L22-68`
- **What this is**: Helper function executing individual tools.
- **Problem**: Returns `(None, "Tool not registered")` for unregistered tools, but allows `DjangoCoreClientError` to raise directly. Path A and Path B must each handle both exception types and error tuple returns.
- **Fix**: Catch exceptions inside `_execute_single_tool` and consistently return `(result, error_str)`.
- **If we skip it**: Tool error handling remains fragmented between `gather` return types and outer `try/except` blocks.

#### 11. `TOOL_DOMAIN_MAP` duplicated across modules
- **Location**: `app/graph/nodes/router.py:L23`, `app/graph/nodes/extractor.py:L41`
- **What this is**: Mapping from tool names to ERP domain names (`accounting` / `inventory`).
- **Problem**: Identical dictionary defined in two separate node files. Adding a new tool requires updating both files.
- **Fix**: Move `TOOL_DOMAIN_MAP` to `app/schemas/tools.py` alongside `TOOL_SCHEMA_REGISTRY`.
- **If we skip it**: Future tool registrations risk becoming out of sync between routing and extraction.

---

### Group 3: Nice to Have

#### 12. SKU fallback extractor captures trailing dot
- **Location**: `app/graph/nodes/extractor.py:L270-280`
- **What this is**: Regex extracting SKU tokens like `AAEO-NPKG`.
- **Problem**: Character class includes dot (`.`), extracting `"AAEO-NPKG."` if followed by a sentence period.
- **Fix**: Strip trailing punctuation with `.rstrip(".")` on the extracted code.
- **If we skip it**: Sentences ending with an SKU query the backend with a period attached to the code.

#### 13. Empty state hints only display the first tool's filters
- **Location**: `app/graph/nodes/synthesizer.py:L271-285`
- **What this is**: Helper showing which filters were used when 0 records are found after retries.
- **Problem**: Reads only `state.get("extracted_params")`, ignoring parameters from subsequent tools in `active_tools`.
- **Fix**: Iterate through all `active_tools` params to build the filter hints list.
- **If we skip it**: When all tools return empty, the message only cites the first tool's filter settings.

---

## Summary
- **Verdict**: Core multi-tool architecture is functioning and well-tested (375 tests passing), but fix finding 1 (partial error handling) and finding 2 (duplicate tool name collision) before taking high-volume production traffic.
- **Lean**: -35 lines, -0 dependencies possible.
- **Not checked**: Direct live network calls to the external Django ERP instance (mocked client used during test execution).
