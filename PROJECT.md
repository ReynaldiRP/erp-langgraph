# Project: Phase 2 Multi-Tool & Cross-Module Execution Architecture (Supervisor Agent)

## Architecture
The FastPrint ERP AI Orchestrator executes queries through a compiled LangGraph `StateGraph`. Phase 2 introduces cross-module multi-intent orchestration using a Supervisor Agent pattern that coordinates domain subagents for ERP modules (such as Inventory and Accounting), executing within a single conversational turn while maintaining complete backward compatibility with single-tool workflows and ERP multi-company scoping rules (`company_id`).

```
                    ┌───────────────────────────┐
                    │        User Query         │
                    └─────────────┬─────────────┘
                                  │
                                  ▼
                    ┌───────────────────────────┐
                    │        router_node        │
                    │  (Supervisor & Planner)   │
                    └─────────────┬─────────────┘
                                  │
                                  ▼
                    ┌───────────────────────────┐
                    │      extractor_node       │
                    │ (Domain Subagent Schemas) │
                    └─────────────┬─────────────┘
                                  │
                                  ▼
                    ┌───────────────────────────┐
                    │         tool_node         │
                    │ (Concurrent Dispatcher)   │
                    └─────────────┬─────────────┘
                                  │
                                  ▼
                    ┌───────────────────────────┐
                    │       verifier_node       │
                    │   (Aggregate Verifier)    │
                    └──────┬─────────────┬──────┘
                           │             │
        (Retry < 2 & Empty)│             │(Valid / Error / Empty exhausted)
                           ▼             ▼
                    ┌─────────────┐┌────────────┐
                    │  Extractor  ││synthesizer │
                    │ (Broadened) ││ (Unified)  │
                    └─────────────┘└────────────┘
```

## Feature Inventory
Every required feature identified in requirements and explorer surveys is mapped below:

| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | `ToolCallPlan` & Extended `AgentState` | Define `ToolCallPlan` TypedDict and extend `AgentState` with `active_tools`, `detected_domains`, `detected_tools`, `multi_tool_results`, `multi_verification_status`, and `is_multi_tool` while preserving scalar backward compatibility fields. | M1 | Survey 1 & 2 |
| 2 | Multi-Intent Supervisor Routing | Upgrade `classify_query_rule_based` and `router_node` to detect multiple intents across inventory and accounting, populating `active_tools` while retaining guardrails and single-tool fallback. | M1 | Survey 1 & 2 |
| 3 | Domain Subagent Parameter Extraction | Enhance `extractor_node` to iterate through planned tools in `active_tools`, using domain schemas (`TOOL_SCHEMA_REGISTRY`) with focused subagent parameter extraction and robust fallbacks. | M2 | Survey 1 & 2 |
| 4 | Concurrent Tool Dispatcher | Implement multi-tool dispatch in `tool_node` using `asyncio.gather(*tasks, return_exceptions=True)` to execute independent ERP tool calls concurrently while preserving `DjangoCoreClient` resource lifecycle and legacy `tool_result`. | M3 | Survey 1 & 3 |
| 5 | Aggregate Verification | Enhance `verifier_node` to evaluate record counts and status across all executed tools in `multi_tool_results`, handling complete, partial, empty, and error conditions with targeted retry instructions. | M4 | Survey 1 & 3 |
| 6 | Unified Cross-Module Synthesizer | Upgrade `synthesizer_node` to format multi-domain results into unified Indonesian Markdown with dedicated Inventory and Accounting sections, IDR currency formatting (`Rp`), and reference tables. | M4 | Survey 1 & 3 |
| 7 | Multi-Tool Unit & Integration Test Suite | Author `tests/test_multi_tool_execution.py` testing routing, extraction, parallel execution, aggregate verification, unified synthesis, and end-to-end multi-intent execution. | M5 | Survey 1, 2, 3 |
| 8 | Backward Compatibility & Regression Suite | Verify all 273 existing unit tests pass without regressions, validating single-tool queries and strict multi-company `company_id` scoping. | M5 | Survey 3 |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| 1 | M1: State & Multi-Domain Router | Extend `AgentState` and `create_initial_state` in `app/graph/state.py`; implement multi-intent routing and `MultiRouterDecision` in `app/graph/nodes/router.py`. | none | DONE |
| 2 | M2: Multi-Tool Parameter Extractor | Enhance `app/graph/nodes/extractor.py` to extract parameters across domain subagents for all planned calls in `active_tools`. | M1 | DONE |
| 3 | M3: Concurrent Tool Dispatcher | Implement concurrent dispatch in `app/graph/nodes/tool_node.py` over `active_tools` using `asyncio.gather`, handling per-tool exceptions and populating `multi_tool_results`. | M2 | DONE |
| 4 | M4: Aggregate Verifier & Unified Synthesizer | Enhance `app/graph/nodes/verifier.py` for aggregate status evaluation and `app/graph/nodes/synthesizer.py` for multi-domain Markdown and IDR tables. | M3 | DONE |
| 5 | M5: Test Suite & End-to-End Verification | Build comprehensive tests in `tests/test_multi_tool_execution.py` and verify all 273 existing tests pass. | M4 | DONE |

## Interface Contracts
### `AgentState` Schema Contract
- `ToolCallPlan(TypedDict, total=False)`:
  - `tool: str`: ERP tool name (e.g., `get_stock_level`, `get_account_balance`)
  - `domain: str`: ERP domain (`inventory`, `accounting`)
  - `sub_query: Optional[str]`: Focused subagent slice of user query
  - `params: Optional[Dict[str, Any]]`: Validated tool parameters
  - `status: Optional[str]`: Execution status (`pending`, `success`, `error`)
- `AgentState`:
  - `is_multi_tool: Optional[bool]`
  - `detected_domains: Optional[List[str]]`
  - `detected_tools: Optional[List[str]]`
  - `active_tools: Optional[List[ToolCallPlan]]`
  - `multi_tool_results: Optional[Dict[str, Any]]`
  - `multi_verification_status: Optional[Dict[str, str]]`
  - Legacy fields synchronized: `detected_domain = active_tools[0]["domain"]`, `detected_tool = active_tools[0]["tool"]`, `extracted_params = active_tools[0]["params"]`, `tool_result = multi_tool_results[detected_tool]`.

### Router ↔ Extractor Contract
- Router returns:
  `{"detected_domains": [...], "detected_tools": [...], "active_tools": [...], "is_multi_tool": bool, "detected_domain": ..., "detected_tool": ...}`
- Extractor accepts `state["active_tools"]`, iterates and updates each item with validated `params`, and returns:
  `{"active_tools": [...], "extracted_params": active_tools[0]["params"]}`

### Extractor ↔ Tool Node Contract
- Tool Node accepts `state["active_tools"]` and `state["company_id"]`.
- Concurrently awaits all handlers: `await asyncio.gather(*tasks, return_exceptions=True)`.
- Updates each `active_tools[i]["status"] = "success" | "error"`.
- Returns: `{"multi_tool_results": {tool_name: result_or_error}, "tool_result": multi_tool_results[primary_tool], "active_tools": [...]}`.

### Tool Node ↔ Verifier Contract
- Verifier accepts `state["multi_tool_results"]` and `state["active_tools"]`.
- Evaluates per-tool record count.
- If all tools returned records: `verification_status = VALID`.
- If any tool returned 0 records and `retry_count < 2`: `verification_status = RETRY`, `retry_instruction = "..."`.
- If retry exhausted: returns `VALID` if at least one tool succeeded, or `EMPTY` if all empty.
- Returns: `{"verification_status": ..., "multi_verification_status": {...}, "retry_count": ..., "retry_instruction": ...}`.

### Verifier ↔ Synthesizer Contract
- Synthesizer checks `state.get("is_multi_tool")` or `len(state.get("active_tools", [])) > 1`.
- If single tool: formats using deterministic single-tool tables.
- If multi tool: formats Markdown sections for each tool in `multi_tool_results`, highlighting stock counts, balance in IDR (`Rp`), and summary.
- Returns: `{"final_answer": formatted_markdown}`.

## Code Layout
- `app/graph/state.py`: State definition and initialization.
- `app/graph/nodes/router.py`: Supervisor and intent planner.
- `app/graph/nodes/extractor.py`: Domain subagent parameter extraction.
- `app/graph/nodes/tool_node.py`: Concurrent tool dispatcher.
- `app/graph/nodes/verifier.py`: Aggregate result verifier.
- `app/graph/nodes/synthesizer.py`: Unified cross-module response synthesizer.
- `app/graph/workflow.py`: LangGraph StateGraph edges and routing conditions.
- `tests/test_multi_tool_execution.py`: New comprehensive test suite.
