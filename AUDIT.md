# Architecture & Code Quality Audit: FastPrint ERP AI Orchestrator

## Overview
This repository provides an autonomous LangGraph orchestrator that answers accounting and inventory business queries. It interfaces with FastPrint ERP Django's `ai_tools` layer via `DjangoCoreClient` / `ERPClient`.

---

## Findings & Remediation Progress

### Group 0: Must Fix (Critical)

#### [RESOLVED] Missing multi-tool collision handling
- **Location**: `app/graph/nodes/tool_node.py`, `app/graph/nodes/synthesizer.py`
- **Resolution**: When identical tools are planned within `active_tools`, `tool_node` partitions outputs using composite keys (`f"{tool_name}_{idx}"`) and attaches results to plan dictionaries. `synthesizer_node` reads each plan's distinct result, guaranteeing all products or queries render without collisions.

#### [RESOLVED] Empty invoices in multi-tool output render an empty heading
- **Location**: `app/graph/nodes/synthesizer.py:L74-78`
- **Resolution**: Added explicit fallback message `lines.append("Tidak ada transaksi tercatat pada periode ini.")` when `len(entries) == 0`. Prevents printing bare section headers without body text in multi-tool outputs. Verified with unit and multi-tool tests (`test_format_vendor_invoices_empty_entries` and `test_unified_synthesizer_multi_tool_with_empty_invoice`).

#### [RESOLVED] `tool_node_sync` crashes if passed an active async client
- **Location**: `app/graph/nodes/tool_node.py:L252-270`
- **Resolution**: Wrapped the worker thread coroutine to inspect `client`. If `client` is an active `DjangoCoreClient` (not a mock) with an `httpx.AsyncClient` bound to the outer loop, a fresh `DjangoCoreClient` is initialized inside the worker thread's event loop with identical credentials and configuration, and safely closed with `async with`. Mocks and None clients pass through intact. Verified in `test_tool_node_sync_with_active_client_in_running_loop` and `test_tool_node_sync_with_mock_client_in_running_loop`.

#### [RESOLVED] `_execute_single_tool` mixes exceptions and error tuples
- **Location**: `app/graph/nodes/tool_node.py:L55-75`
- **Resolution**: Wrapped handler execution in `try/except` inside `_execute_single_tool`, catching `DjangoCoreClientError` and generic `Exception`, consistently returning `(None, error_str)` or `(result, None)`. Verified in `TestExecuteSingleToolDirect`.

#### [RESOLVED] `TOOL_DOMAIN_MAP` duplicated across modules
- **Location**: `app/schemas/tools.py:L285-298`, `app/graph/nodes/router.py`, `app/graph/nodes/extractor.py`
- **Resolution**: Centralized `TOOL_DOMAIN_MAP` in `app/schemas/tools.py` and imported it in `router.py` and `extractor.py`, eliminating dictionary duplication across nodes. Verified in `TestToolDomainMapRegistry`.

---

### Group 1: Should Fix
*(All items in Group 1 resolved)*

---

### Group 2: Nice to Have

#### 1. Empty state hints only display the first tool's filters
- **Location**: `app/graph/nodes/synthesizer.py:L273-287`
- **What this is**: Helper showing which filters were used when 0 records are found after retries.
- **Problem**: Reads only `state.get("extracted_params")`, ignoring parameters from subsequent tools in `active_tools`.
- **Fix**: Iterate through all `active_tools` params to build the filter hints list.
- **If we skip it**: When all tools return empty, the message only cites the first tool's filter settings.

---

## Summary
- **Verdict**: Healthy. All critical (Must Fix) bugs and high-priority cross-thread/rendering defects resolved, and tool exception handling and domain registries consolidated.
- **Lean**: -18 lines, -0 dependencies possible.
- **Not checked**: Direct live network calls to external Django ERP instances (mocked client used during test execution).
