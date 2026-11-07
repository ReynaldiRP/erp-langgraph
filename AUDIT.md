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

### Resolved Issues (2026-11-07)

#### [RESOLVED] Multi-word product names truncated in stock fallback extractor
- **Location**: `app/graph/nodes/extractor.py`
- **Resolution**: Updated `_fallback_extract_stock_level` regex to capture multi-word tokens delimited by boundary keywords (`di`, `pada`, `ke`, `gudang`, `lokasi`, `dan`, `serta`, etc.) or sentence end. Added negative lookahead to prevent false-positive captures of locations, financial terms, and customer references. Stripped trailing punctuation/dots. Verified across single- and multi-tool tests.

#### [RESOLVED] Trailing punctuation and emoji break vendor extraction
- **Location**: `app/graph/nodes/extractor.py`
- **Resolution**: Stripped trailing punctuation, symbols, and Unicode emojis (`re.sub(r"[^\w\)]+$", "", ...)`). Extended delimiter pattern to handle intervening punctuation (`[,?!;:/\-]+`) before vendor delimiter keywords. Verified with question marks, emojis, and combined edge cases.

#### [RESOLVED] SKU fallback extractor captures trailing dot
- **Location**: `app/graph/nodes/extractor.py`
- **Resolution**: Product code tokens are cleaned of sentence-ending periods with `.rstrip(".")` and sentence-end delimiter recognition, passing adversarial injection tests cleanly.

#### [RESOLVED] `_calculate_records_count` defaults unexpected dicts to 1 record
- **Location**: `app/graph/nodes/verifier.py`
- **Resolution**: Added checks for explicit error indicators (`error`, `detail`, `errors`, `success: False`), added support for the `locations` collection key, and verified presence of known ERP business record attributes (`KNOWN_RECORD_ATTRS`) before evaluating single dictionary records as valid. Unrecognized dictionaries or dicts with all None values now evaluate to 0.

#### [RESOLVED] Partial tool failure handled inconsistently depending on list order
- **Location**: `app/graph/nodes/verifier.py`, `app/graph/nodes/tool_node.py`
- **Resolution**: Multi-tool verification in `verifier_node` now evaluates `multi_verification_status` and plan execution results directly instead of relying on scalar `tool_result is None`. Turns with at least one successful tool returning data are marked `VALID` regardless of list ordering.

#### [RESOLVED] Duplicate tool names overwrite each other in multi-tool results
- **Location**: `app/graph/nodes/tool_node.py`, `app/graph/nodes/synthesizer.py`
- **Resolution**: When identical tools are planned within `active_tools`, `tool_node` partitions outputs using composite keys (`f"{tool_name}_{idx}"`) and attaches results to plan dictionaries. `synthesizer_node` reads each plan's distinct result, guaranteeing all products or queries render without collisions.

---

### Group 1: Should Fix

#### 1. Anaphoric clitic `-nya` does not trigger conversational history lookup
- **Location**: `app/graph/nodes/extractor.py:L274-282`
- **What this is**: Multi-turn extraction resolving prior entities when user says `"Berapa stoknya"`.
- **Problem**: History lookup checks for words like `"itu"` and `"tadi"`, but omits `-nya` and `stoknya`, leaving product code `None`.
- **Fix**: Include `has_anaphoric_reference(sub_query)` when deciding to inspect conversation history for stock extraction.
- **If we skip it**: Follow-up questions like `"Berapa stoknya?"` fail to look up the product discussed in the previous turn.

#### 2. Location fallback extractor captures `"gudang"` as location name
- **Location**: `app/graph/nodes/extractor.py:L285-295`
- **What this is**: Fallback extractor resolving warehouse location queries.
- **Problem**: In `"lokasi gudang utama"`, the regex matches `"gudang"` as the search term instead of `"utama"`.
- **Fix**: Strip leading stop words (`gudang`, `lokasi`, `warehouse`) from the matched group before returning `search`.
- **If we skip it**: Filtering by warehouse name searches for the word `"gudang"` rather than the actual warehouse label.

#### 3. Empty invoices in multi-tool output render an empty heading
- **Location**: `app/graph/nodes/synthesizer.py:L60-78`
- **What this is**: Synthesizer formatter for vendor invoices and journal entries.
- **Problem**: When an invoice query returns 0 entries, `entries` is empty, so table creation is skipped, printing only `### Laporan Transaksi Tagihan / Jurnal (0 Catatan Ditemukan)` with no explanatory body text.
- **Fix**: Add an explicit fallback message: `lines.append("Tidak ada transaksi tercatat pada periode ini.")` when `len(entries) == 0`.
- **If we skip it**: Users see an empty section header without explanation.

#### 4. `tool_node_sync` crashes if passed an active async client
- **Location**: `app/graph/nodes/tool_node.py:L240-252`
- **What this is**: Synchronous wrapper for executing `tool_node`.
- **Problem**: `ThreadPoolExecutor` runs `asyncio.run()` in a new thread. If the caller passed an existing `DjangoCoreClient`, its internal `httpx.AsyncClient` is bound to the outer loop and throws a cross-loop `RuntimeError`.
- **Fix**: When running inside an active loop, pass `client=None` to ensure the worker thread initializes its own client, or run `loop.run_until_complete`.
- **If we skip it**: Running synchronous tests or CLI helpers with an injected client crashes with event loop conflicts.

#### 5. `_execute_single_tool` mixes exceptions and error tuples
- **Location**: `app/graph/nodes/tool_node.py:L22-68`
- **What this is**: Helper function executing individual tools.
- **Problem**: Returns `(None, "Tool not registered")` for unregistered tools, but allows `DjangoCoreClientError` to raise directly. Path A and Path B must each handle both exception types and error tuple returns.
- **Fix**: Catch exceptions inside `_execute_single_tool` and consistently return `(result, error_str)`.
- **If we skip it**: Tool error handling remains fragmented between `gather` return types and outer `try/except` blocks.

#### 6. `TOOL_DOMAIN_MAP` duplicated across modules
- **Location**: `app/graph/nodes/router.py:L23`, `app/graph/nodes/extractor.py:L41`
- **What this is**: Mapping from tool names to ERP domain names (`accounting` / `inventory`).
- **Problem**: Identical dictionary defined in two separate node files. Adding a new tool requires updating both files.
- **Fix**: Move `TOOL_DOMAIN_MAP` to `app/schemas/tools.py` alongside `TOOL_SCHEMA_REGISTRY`.
- **If we skip it**: Future tool registrations risk becoming out of sync between routing and extraction.

---

### Group 2: Nice to Have

#### 7. Empty state hints only display the first tool's filters
- **Location**: `app/graph/nodes/synthesizer.py:L271-285`
- **What this is**: Helper showing which filters were used when 0 records are found after retries.
- **Problem**: Reads only `state.get("extracted_params")`, ignoring parameters from subsequent tools in `active_tools`.
- **Fix**: Iterate through all `active_tools` params to build the filter hints list.
- **If we skip it**: When all tools return empty, the message only cites the first tool's filter settings.

---

## Summary
- **Verdict**: Healthy. All critical (Must Fix) bugs resolved, and primary fallback extractor regressions resolved (382 tests passing with zero failures, 2 expected failures).
- **Lean**: -18 lines, -0 dependencies possible.
- **Not checked**: Direct live network calls to external Django ERP instances (mocked client used during test execution).
