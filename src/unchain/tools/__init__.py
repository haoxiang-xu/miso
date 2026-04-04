from __future__ import annotations

import importlib

__all__ = [
    "AnthropicMessageBuilder",
    "BaseToolHarness",
    "CATALOG_TOOL_NAMES",
    "execute_confirmable_tool_call",
    "GeminiMessageBuilder",
    "HistoryPayloadOptimizer",
    "HumanInputResumeHarness",
    "inject_observation",
    "NormalizedToolHistoryRecord",
    "OBSERVATION_MAX_OUTPUT_TOKENS",
    "OBSERVATION_RECENT_MESSAGES",
    "OBSERVATION_SYSTEM_PROMPT",
    "OllamaMessageBuilder",
    "observation_token_state",
    "OpenAIMessageBuilder",
    "ProviderMessageBuilder",
    "TOOLKIT_ACTIVATE_TOOL_NAME",
    "TOOLKIT_DEACTIVATE_TOOL_NAME",
    "TOOLKIT_DESCRIBE_TOOL_NAME",
    "TOOLKIT_LIST_ACTIVE_TOOL_NAME",
    "TOOLKIT_LIST_TOOL_NAME",
    "Tool",
    "tool",
    "ToolBatchState",
    "ToolConfirmationRequest",
    "ToolConfirmationResponse",
    "ToolConfirmationPolicy",
    "ToolContext",
    "ToolDescriptor",
    "ToolExecutionHarness",
    "ToolExecutionContext",
    "ToolExecutionOutcome",
    "ToolHarness",
    "ToolHistoryOptimizationContext",
    "ToolParameter",
    "ToolRegistryConfig",
    "ToolRuntimeOutcome",
    "ToolRuntimePlugin",
    "Toolkit",
    "ToolkitCatalogConfig",
    "ToolkitCatalogRuntime",
    "ToolkitDescriptor",
    "ToolkitRegistry",
    "build_visible_toolkits",
    "extract_toolkit_catalog_token",
    "get_provider_message_builder",
    "get_toolkit_metadata",
    "list_toolkits",
]

_EXPORT_TO_MODULE = {
    "CATALOG_TOOL_NAMES": ".catalog",
    "TOOLKIT_ACTIVATE_TOOL_NAME": ".catalog",
    "TOOLKIT_DEACTIVATE_TOOL_NAME": ".catalog",
    "TOOLKIT_DESCRIBE_TOOL_NAME": ".catalog",
    "TOOLKIT_LIST_ACTIVE_TOOL_NAME": ".catalog",
    "TOOLKIT_LIST_TOOL_NAME": ".catalog",
    "ToolkitCatalogConfig": ".catalog",
    "ToolkitCatalogRuntime": ".catalog",
    "build_visible_toolkits": ".catalog",
    "extract_toolkit_catalog_token": ".catalog",
    "BaseToolHarness": ".base",
    "ToolContext": ".base",
    "ToolHarness": ".base",
    "ToolExecutionOutcome": ".confirmation",
    "execute_confirmable_tool_call": ".confirmation",
    "tool": ".decorators",
    "ToolExecutionHarness": ".execution",
    "HumanInputResumeHarness": ".human_input",
    "AnthropicMessageBuilder": ".messages",
    "GeminiMessageBuilder": ".messages",
    "OpenAIMessageBuilder": ".messages",
    "OllamaMessageBuilder": ".messages",
    "ProviderMessageBuilder": ".messages",
    "get_provider_message_builder": ".messages",
    "HistoryPayloadOptimizer": ".models",
    "NormalizedToolHistoryRecord": ".models",
    "ToolConfirmationRequest": ".models",
    "ToolConfirmationPolicy": ".models",
    "ToolConfirmationResponse": ".models",
    "ToolExecutionContext": ".models",
    "ToolHistoryOptimizationContext": ".models",
    "ToolParameter": ".models",
    "OBSERVATION_MAX_OUTPUT_TOKENS": ".observation",
    "OBSERVATION_RECENT_MESSAGES": ".observation",
    "OBSERVATION_SYSTEM_PROMPT": ".observation",
    "inject_observation": ".observation",
    "observation_token_state": ".observation",
    "ToolDescriptor": ".registry",
    "ToolRegistryConfig": ".registry",
    "ToolkitDescriptor": ".registry",
    "ToolkitRegistry": ".registry",
    "get_toolkit_metadata": ".registry",
    "list_toolkits": ".registry",
    "ToolRuntimeOutcome": ".runtime",
    "ToolRuntimePlugin": ".runtime",
    "Tool": ".tool",
    "Toolkit": ".toolkit",
    "ToolBatchState": ".types",
}

tool = importlib.import_module(".decorators", __name__).tool


def __getattr__(name: str):
    if name == "tool":
        return tool
    module_name = _EXPORT_TO_MODULE.get(name)
    if module_name is None:
        raise AttributeError(name)
    module = importlib.import_module(module_name, __name__)
    return getattr(module, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
