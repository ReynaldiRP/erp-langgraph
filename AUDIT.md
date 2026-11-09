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

#### [RESOLVED] Anaphoric clitic `-nya` does not trigger conversational history lookup
- **Location**: `app/graph/nodes/extractor.py`
- **Resolution**: Added `has_anaphoric_reference(cleaned)` check in `_fallback_extract_stock_level` and `_fallback_extract_product_info` before history inspection. Filtered `prior_msgs` to exclude the current turn when searching conversation history, correctly extracting codes discussed in prior turns (`test_ellipsis_from_chat_history_with_accounting_tool` passing cleanly).

#### [RESOLVED] Location fallback extractor captures `"gudang"` as location name
- **Location**: `app/graph/nodes/extractor.py`
- **Resolution**: Refined `_fallback_extract_locations` pattern to tolerate repeated warehouse prefixes (`(?:(?:gudang|lokasi|warehouse)\s+)+`) and stripped location stopwords (`gudang`, `lokasi`, `warehouse`, `apa`, `daftar`, etc.). Correctly extracts target search tokens like `"utama"` from `"lokasi gudang utama"` (`test_journal_entries_and_locations` passing cleanly).

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

#### [RESOLVED] Empty invoices in multi-tool output render an empty heading
- **Location**: `app/graph/nodes/synthesizer.py:L74-78`
- **Resolution**: Added explicit fallback message `lines.append("Tidak ada transaksi tercatat pada periode ini.")` when `len(entries) == 0`. Prevents printing bare section headers without body text in multi-tool outputs. Verified with unit and multi-tool tests (`test_format_vendor_invoices_empty_entries` and `test_unified_synthesizer_multi_tool_with_empty_invoice`).

#### [RESOLVED] `tool_node_sync` crashes if passed an active async client
- **Location**: `app/graph/nodes/tool_node.py:L252-270`
- **Resolution**: Wrapped the worker thread coroutine to inspect `client`. If `client` is an active `DjangoCoreClient` (not a mock) with an `httpx.AsyncClient` bound to the outer loop, a fresh `DjangoCoreClient` is initialized inside the worker thread's event loop with identical credentials and configuration, and safely closed with `async with`. Mocks and None clients pass through intact. Verified in `test_tool_node_sync_with_active_client_in_running_loop` and `test_tool_node_sync_with_mock_client_in_running_loop`.

---

### Group 1: Should Fix

#### 1. `_execute_single_tool` mixes exceptions and error tuples
- **Location**: `app/graph/nodes/tool_node.py:L22-68`
- **What this is**: Helper function executing individual tools.
- **Problem**: Returns `(None, "Tool not registered")` for unregistered tools, but allows `DjangoCoreClientError` to raise directly. Path A and Path B must each handle both exception types and error tuple returns.
- **Fix**: Catch exceptions inside `_execute_single_tool` and consistently return `(result, error_str)`.
- **If we skip it**: Tool error handling remains fragmented between `gather` return types and outer `try/except` blocks.

#### 2. `TOOL_DOMAIN_MAP` duplicated across modules
- **Location**: `app/graph/nodes/router.py:L23`, `app/graph/nodes/extractor.py:L41`
- **What this is**: Mapping from tool names to ERP domain names (`accounting` / `inventory`).
- **Problem**: Identical dictionary defined in two separate node files. Adding a new tool requires updating both files.
- **Fix**: Move `TOOL_DOMAIN_MAP` to `app/schemas/tools.py` alongside `TOOL_SCHEMA_REGISTRY`.
- **If we skip it**: Future tool registrations risk becoming out of sync between routing and extraction.

---

### Group 2: Nice to Have

#### 3. Empty state hints only display the first tool's filters
- **Location**: `app/graph/nodes/synthesizer.py:L273-287`
- **What this is**: Helper showing which filters were used when 0 records are found after retries.
- **Problem**: Reads only `state.get("extracted_params")`, ignoring parameters from subsequent tools in `active_tools`.
- **Fix**: Iterate through all `active_tools` params to build the filter hints list.
- **If we skip it**: When all tools return empty, the message only cites the first tool's filter settings.

---

## Summary
- **Verdict**: Healthy. All critical (Must Fix) bugs and high-priority cross-thread/rendering defects resolved (386 tests passing with zero failures).
- **Lean**: -8 lines, -0 dependencies possible.
- **Not checked**: Direct live network calls to external Django ERP instances (mocked client used during test execution).
