"""
Dynamic Auto-Discovery Tool Loader for LangChain / LangGraph.
Calls GET /ai/tools/ on ERP and converts discovered tool metadata into LangChain StructuredTool instances.
"""
import logging
from typing import Any, Dict, List, Optional
from langchain_core.tools import StructuredTool
from pydantic import create_model, Field

from .client import ERPClient

logger = logging.getLogger("tools_dynamic")


def _json_type_to_python(prop_schema: Dict[str, Any]):
    """Map JSON Schema primitive types to Python types."""
    jtype = prop_schema.get("type", "string")
    if jtype == "integer":
        return Optional[int]
    elif jtype == "number":
        return Optional[float]
    elif jtype == "boolean":
        return Optional[bool]
    elif jtype == "array":
        return Optional[list]
    elif jtype == "object":
        return Optional[dict]
    return Optional[str]


def create_dynamic_tools(client: ERPClient) -> List[StructuredTool]:
    """
    Fetch all registered tools from ERP /ai/tools/ and construct LangChain StructuredTools.
    """
    discovered_tools = client.list_tools()
    dynamic_tools: List[StructuredTool] = []

    for tool_meta in discovered_tools:
        domain = tool_meta.get("domain", "general")
        name = tool_meta.get("name", "unnamed_tool")
        description = tool_meta.get("description", "")
        is_write = tool_meta.get("is_write", False)
        params_schema = tool_meta.get("params_schema", {})

        tool_full_name = f"{domain}_{name}"
        properties = params_schema.get("properties", {})
        required_fields = set(params_schema.get("required", []))

        # Build dynamic Pydantic fields
        pydantic_fields: Dict[str, Any] = {
            "company_id": (Optional[int], Field(None, description="Company ID (scoped database ID)"))
        }

        if is_write:
            pydantic_fields["confirm"] = (
                bool,
                Field(False, description="Explicit human confirmation flag required for write/update operations"),
            )

        for prop_name, prop_meta in properties.items():
            py_type = _json_type_to_python(prop_meta)
            desc = prop_meta.get("description", "")
            if prop_name in required_fields:
                pydantic_fields[prop_name] = (py_type, Field(..., description=desc))
            else:
                pydantic_fields[prop_name] = (py_type, Field(None, description=desc))

        # Create Pydantic input model
        args_schema = create_model(f"{tool_full_name}_Input", **pydantic_fields)

        # Factory function capturing current domain, name, is_write
        def make_tool_fn(d: str, n: str, write_flag: bool):
            def execute_fn(**kwargs) -> Any:
                company_id = kwargs.pop("company_id", None)
                confirm = kwargs.pop("confirm", False) if write_flag else False
                # Filter out None values
                clean_params = {k: v for k, v in kwargs.items() if v is not None}
                return client.execute_tool(
                    domain=d,
                    tool_name=n,
                    params=clean_params,
                    company_id=company_id,
                    confirm=confirm,
                )

            return execute_fn

        structured_tool = StructuredTool.from_function(
            func=make_tool_fn(domain, name, is_write),
            name=tool_full_name,
            description=description,
            args_schema=args_schema,
        )
        # Store write flag on tool instance for graph HITL inspection
        setattr(structured_tool, "is_write", is_write)
        dynamic_tools.append(structured_tool)

    logger.info(f"Dynamically registered {len(dynamic_tools)} ERP tools into LangChain.")
    return dynamic_tools
