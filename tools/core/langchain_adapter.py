"""
LangChain Adapter - Bridge between WeaverTool and LangChain

This module provides adapters to make WeaverTool instances compatible
with LangChain's tool ecosystem, allowing seamless integration.

Key features:
- Convert WeaverTool to LangChain BaseTool
- Convert LangChain tool results to ToolResult
- Preserve all metadata and schemas
- Support both sync and async execution
"""

import logging
from collections.abc import Callable
from typing import Any, Optional

from langchain_core.tools import BaseTool, StructuredTool, ToolException
from pydantic import BaseModel, Field, create_model

from tools.core.base import ToolResult, WeaverTool, validate_tool_result

logger = logging.getLogger(__name__)


def create_pydantic_model_from_schema(
    schema: dict[str, Any], model_name: str = "DynamicInput"
) -> type[BaseModel]:
    """
    Create a Pydantic model from a JSON schema.

    LangChain's StructuredTool requires Pydantic models for input validation.
    This function converts our tool_schema format to Pydantic.

    Args:
        schema: JSON schema dict (parameters section)
        model_name: Name for the generated model

    Returns:
        Pydantic model class
    """
    if not schema or schema.get("type") != "object":
        # Fallback: no parameters
        return create_model(model_name)

    properties = schema.get("properties", {})
    required = set(schema.get("required", []))

    # Build field definitions
    fields = {}
    for prop_name, prop_schema in properties.items():
        prop_type = prop_schema.get("type", "string")
        prop_desc = prop_schema.get("description", "")
        prop_default = prop_schema.get("default")

        # Map JSON schema types to Python types
        python_type = {
            "string": str,
            "integer": int,
            "number": float,
            "boolean": bool,
            "array": list,
            "object": dict,
        }.get(prop_type, str)

        # Handle optional vs required
        if prop_name in required:
            if prop_default is not None:
                fields[prop_name] = (
                    python_type,
                    Field(default=prop_default, description=prop_desc),
                )
            else:
                fields[prop_name] = (python_type, Field(..., description=prop_desc))
        else:
            if prop_default is not None:
                fields[prop_name] = (
                    python_type,
                    Field(default=prop_default, description=prop_desc),
                )
            else:
                fields[prop_name] = (
                    Optional[python_type],
                    Field(default=None, description=prop_desc),
                )

    return create_model(model_name, **fields)


def weaver_tool_to_langchain(
    weaver_tool: WeaverTool, method_name: Optional[str] = None, return_direct: bool = False
) -> list[BaseTool]:
    """
    Convert WeaverTool instance to LangChain BaseTool(s).

    Args:
        weaver_tool: WeaverTool instance
        method_name: If specified, only convert this method. Otherwise convert all.
        return_direct: Whether tool output should be returned directly to user

    Returns:
        List of LangChain BaseTool instances
    """
    langchain_tools = []

    # Get all schemas
    schemas = weaver_tool.get_schemas()

    # Filter by method_name if specified
    if method_name:
        if method_name not in schemas:
            logger.warning(f"Method {method_name} not found in {type(weaver_tool).__name__}")
            return []
        schemas = {method_name: schemas[method_name]}

    # Convert each method to a LangChain tool
    for method_key, schema in schemas.items():
        method = weaver_tool.get_method(method_key)
        if not method:
            logger.warning(f"Method {method_key} not found")
            continue

        tool_name = schema.get("name", method_key)
        tool_desc = schema.get("description", f"Executes {method_key}")
        parameters_schema = schema.get("parameters", {})

        # Create wrapper function that handles ToolResult
        def create_wrapper(method_func: Callable) -> Callable:
            def wrapper(**kwargs) -> str:
                """Execute WeaverTool method and convert result."""
                try:
                    result = method_func(**kwargs)

                    # Validate/convert to ToolResult
                    if not isinstance(result, ToolResult):
                        result = validate_tool_result(result)

                    # Return output string (LangChain expects string)
                    return result.output

                except Exception as e:
                    logger.error(f"Tool execution error in {tool_name}: {e!s}")
                    raise ToolException(f"Tool execution failed: {e!s}")

            return wrapper

        # Create Pydantic input model
        try:
            InputModel = create_pydantic_model_from_schema(
                parameters_schema, model_name=f"{tool_name.replace('-', '_').title()}Input"
            )
        except Exception as e:
            logger.warning(f"Failed to create Pydantic model for {tool_name}: {e}")
            # Fallback: use generic model
            InputModel = create_model(f"{tool_name}Input")

        # Create StructuredTool
        try:
            langchain_tool = StructuredTool(
                name=tool_name,
                description=tool_desc,
                func=create_wrapper(method),
                args_schema=InputModel,
                return_direct=return_direct,
            )

            langchain_tools.append(langchain_tool)
            logger.debug(f"Converted {tool_name} to LangChain tool")

        except Exception as e:
            logger.error(f"Failed to create LangChain tool for {tool_name}: {e}")

    return langchain_tools


def langchain_result_to_tool_result(result: Any) -> ToolResult:
    """
    Convert LangChain tool result to ToolResult.

    Args:
        result: Result from LangChain tool execution

    Returns:
        ToolResult instance
    """
    return validate_tool_result(result)


def wrap_langchain_tool_with_tool_result(langchain_tool: BaseTool) -> BaseTool:
    """
    Wrap a LangChain tool to return ToolResult instead of raw output.

    This is useful for legacy LangChain tools that you want to upgrade
    to use the unified ToolResult format.

    Args:
        langchain_tool: Original LangChain tool

    Returns:
        Wrapped tool that returns ToolResult
    """
    original_func = langchain_tool.func

    def wrapped_func(*args, **kwargs) -> str:
        """Wrapped function that converts output to ToolResult."""
        try:
            result = original_func(*args, **kwargs)
            tool_result = validate_tool_result(result)
            return tool_result.to_json()
        except Exception as e:
            tool_result = ToolResult(
                success=False,
                output=str(e),
                error=str(e),
                metadata={"error_type": type(e).__name__},
            )
            return tool_result.to_json()

    # Create new tool with wrapped function
    wrapped_tool = type(langchain_tool)(
        name=langchain_tool.name, description=langchain_tool.description, func=wrapped_func
    )

    return wrapped_tool


def batch_convert_weaver_tools(
    weaver_tools: list[WeaverTool], return_direct: bool = False
) -> list[BaseTool]:
    """
    Convert multiple WeaverTool instances to LangChain tools in batch.

    Args:
        weaver_tools: List of WeaverTool instances
        return_direct: Whether tools should return directly

    Returns:
        Flat list of LangChain BaseTool instances
    """
    all_tools = []

    for weaver_tool in weaver_tools:
        tools = weaver_tool_to_langchain(weaver_tool, return_direct=return_direct)
        all_tools.extend(tools)

    logger.info(f"Converted {len(weaver_tools)} WeaverTools into {len(all_tools)} LangChain tools")
    return all_tools


# Standalone demo helper
def _demo_tool_conversion():
    """Demonstrate WeaverTool → LangChain conversion (requires discoverable tools)."""
    from tools.core.registry import get_global_registry

    registry = get_global_registry()
    names = registry.list_names()
    if not names:
        print("[demo] No registered tools found — run initialize_enhanced_tools() first.")
        return

    weaver_tools = [registry.get(name) for name in names[:2] if registry.get(name)]
    if not weaver_tools:
        print("[demo] No tool instances available.")
        return

    langchain_tools = batch_convert_weaver_tools(weaver_tools)
    print(f"[demo] Converted {len(langchain_tools)} tool(s):")
    for tool in langchain_tools:
        desc = (tool.description or "")[:80]
        print(f"  - {tool.name}: {desc}")


if __name__ == "__main__":
    _demo_tool_conversion()
