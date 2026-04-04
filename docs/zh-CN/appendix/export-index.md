# 导出索引

各包 `__init__` 中声明的导出，以及对应的参考页位置。

## `src/unchain/__init__.py`

| 名称 | 源码 | 参考 |
| --- | --- | --- |
| `Agent` | `src/unchain/agents/agent.py:114` | `../api/agents.md#agent` |
| `Team` | `src/unchain/agents/team.py:11` | `../api/agents.md#team` |
| `__version__` | `-` | 非 class 导出 |

## `src/unchain/_internal/__init__.py`

| 名称 | 源码 | 参考 |
| --- | --- | --- |
| `as_text` | `-` | 非 class 导出 |
| `normalize_mentions` | `-` | 非 class 导出 |

## `src/unchain/agents/__init__.py`

| 名称 | 源码 | 参考 |
| --- | --- | --- |
| `Agent` | `src/unchain/agents/agent.py:114` | `../api/agents.md#agent` |
| `Team` | `src/unchain/agents/team.py:11` | `../api/agents.md#team` |

## `src/unchain/input/__init__.py`

| 名称 | 源码 | 参考 |
| --- | --- | --- |
| `ASK_USER_QUESTION_TOOL_NAME` | `-` | 非 class 导出 |
| `HUMAN_INPUT_KIND_SELECTOR` | `-` | 非 class 导出 |
| `HUMAN_INPUT_OTHER_VALUE` | `-` | 非 class 导出 |
| `HumanInputOption` | `src/unchain/input/human_input.py:61` | `../api/input-workspace-schemas.md#humaninputoption` |
| `HumanInputRequest` | `src/unchain/input/human_input.py:89` | `../api/input-workspace-schemas.md#humaninputrequest` |
| `HumanInputResponse` | `src/unchain/input/human_input.py:225` | `../api/input-workspace-schemas.md#humaninputresponse` |
| `build_ask_user_question_tool` | `-` | 非 class 导出 |
| `is_human_input_tool_name` | `-` | 非 class 导出 |
| `media` | `-` | 非 class 导出 |

## `src/unchain/memory/__init__.py`

| 名称 | 源码 | 参考 |
| --- | --- | --- |
| `ContextStrategy` | `src/unchain/memory/manager.py:84` | `../api/memory.md#contextstrategy` |
| `HybridContextStrategy` | `src/unchain/memory/manager.py:1779` | `../api/memory.md#hybridcontextstrategy` |
| `InMemorySessionStore` | `src/unchain/memory/manager.py:104` | `../api/memory.md#inmemorysessionstore` |
| `JsonFileLongTermProfileStore` | `src/unchain/memory/manager.py:117` | `../api/memory.md#jsonfilelongtermprofilestore` |
| `LastNTurnsStrategy` | `src/unchain/memory/manager.py:1642` | `../api/memory.md#lastnturnsstrategy` |
| `LongTermExtractor` | `-` | 非 class 导出 |
| `LongTermMemoryConfig` | `src/unchain/memory/manager.py:144` | `../api/memory.md#longtermmemoryconfig` |
| `LongTermProfileStore` | `src/unchain/memory/manager.py:52` | `../api/memory.md#longtermprofilestore` |
| `LongTermVectorAdapter` | `src/unchain/memory/manager.py:61` | `../api/memory.md#longtermvectoradapter` |
| `MemoryConfig` | `src/unchain/memory/manager.py:167` | `../api/memory.md#memoryconfig` |
| `MemoryManager` | `src/unchain/memory/manager.py:1866` | `../api/memory.md#memorymanager` |
| `SessionStore` | `src/unchain/memory/manager.py:21` | `../api/memory.md#sessionstore` |
| `SummaryGenerator` | `-` | 非 class 导出 |
| `SummaryTokenStrategy` | `src/unchain/memory/manager.py:1675` | `../api/memory.md#summarytokenstrategy` |
| `VectorStoreAdapter` | `src/unchain/memory/manager.py:30` | `../api/memory.md#vectorstoreadapter` |

## `src/unchain/runtime/__init__.py`

| 名称 | 源码 | 参考 |
| --- | --- | --- |
| `Broth` | `src/unchain/runtime/engine.py:103` | `../api/runtime.md#broth` |
| `DEFAULT_PAYLOADS_RESOURCE` | `-` | 非 class 导出 |
| `MODEL_CAPABILITIES_RESOURCE` | `-` | 非 class 导出 |
| `ProviderTurnResult` | `src/unchain/runtime/engine.py:74` | `../api/runtime.md#providerturnresult` |
| `ToolCall` | `src/unchain/runtime/engine.py:68` | `../api/runtime.md#toolcall` |
| `ToolExecutionOutcome` | `src/unchain/runtime/engine.py:93` | `../api/runtime.md#toolexecutionoutcome` |
| `load_default_payloads` | `-` | 非 class 导出 |
| `load_model_capabilities` | `-` | 非 class 导出 |

## `src/unchain/schemas/__init__.py`

| 名称 | 源码 | 参考 |
| --- | --- | --- |
| `ResponseFormat` | `src/unchain/schemas/response.py:7` | `../api/input-workspace-schemas.md#responseformat` |

## `src/unchain/toolkits/__init__.py`

| 名称 | 源码 | 参考 |
| --- | --- | --- |
| `AskUserToolkit` | `src/unchain/toolkits/builtin/ask_user/ask_user.py:7` | `../api/toolkits.md#askusertoolkit` |
| `BuiltinToolkit` | `src/unchain/toolkits/base.py:10` | `../api/toolkits.md#builtintoolkit` |
| `ExternalAPIToolkit` | `src/unchain/toolkits/builtin/external_api/external_api.py:12` | `../api/toolkits.md#externalapitoolkit` |
| `MCPToolkit` | `src/unchain/toolkits/mcp.py:62` | `../api/toolkits.md#mcptoolkit` |
| `TerminalToolkit` | `src/unchain/toolkits/builtin/terminal/terminal.py:10` | `../api/toolkits.md#terminaltoolkit` |
| `WorkspaceToolkit` | `src/unchain/toolkits/builtin/workspace/workspace.py:24` | `../api/toolkits.md#workspacetoolkit` |

## `src/unchain/tools/__init__.py`

| 名称 | 源码 | 参考 |
| --- | --- | --- |
| `CATALOG_TOOL_NAMES` | `-` | 非 class 导出 |
| `HistoryPayloadOptimizer` | `-` | 非 class 导出 |
| `NormalizedToolHistoryRecord` | `src/unchain/tools/models.py:191` | `../api/tools.md#normalizedtoolhistoryrecord` |
| `TOOLKIT_ACTIVATE_TOOL_NAME` | `-` | 非 class 导出 |
| `TOOLKIT_DEACTIVATE_TOOL_NAME` | `-` | 非 class 导出 |
| `TOOLKIT_DESCRIBE_TOOL_NAME` | `-` | 非 class 导出 |
| `TOOLKIT_LIST_ACTIVE_TOOL_NAME` | `-` | 非 class 导出 |
| `TOOLKIT_LIST_TOOL_NAME` | `-` | 非 class 导出 |
| `Tool` | `src/unchain/tools/tool.py:16` | `../api/tools.md#tool` |
| `ToolConfirmationRequest` | `src/unchain/tools/models.py:209` | `../api/tools.md#toolconfirmationrequest` |
| `ToolConfirmationResponse` | `src/unchain/tools/models.py:233` | `../api/tools.md#toolconfirmationresponse` |
| `ToolDescriptor` | `src/unchain/tools/registry.py:222` | `../api/tools.md#tooldescriptor` |
| `ToolHistoryOptimizationContext` | `src/unchain/tools/models.py:178` | `../api/tools.md#toolhistoryoptimizationcontext` |
| `ToolParameter` | `src/unchain/tools/models.py:155` | `../api/tools.md#toolparameter` |
| `ToolRegistryConfig` | `src/unchain/tools/registry.py:192` | `../api/tools.md#toolregistryconfig` |
| `Toolkit` | `src/unchain/tools/toolkit.py:9` | `../api/tools.md#toolkit` |
| `ToolkitCatalogConfig` | `src/unchain/tools/catalog.py:34` | `../api/tools.md#toolkitcatalogconfig` |
| `ToolkitCatalogRuntime` | `src/unchain/tools/catalog.py:76` | `../api/tools.md#toolkitcatalogruntime` |
| `ToolkitDescriptor` | `src/unchain/tools/registry.py:286` | `../api/tools.md#toolkitdescriptor` |
| `ToolkitRegistry` | `src/unchain/tools/registry.py:378` | `../api/tools.md#toolkitregistry` |
| `build_visible_toolkits` | `-` | 非 class 导出 |
| `extract_toolkit_catalog_token` | `-` | 非 class 导出 |
| `get_toolkit_metadata` | `-` | 非 class 导出 |
| `list_toolkits` | `-` | 非 class 导出 |
| `tool` | `-` | 非 class 导出 |

## `src/unchain/workspace/__init__.py`

| 名称 | 源码 | 参考 |
| --- | --- | --- |
| `ANCHOR_MATCH_WINDOW` | `-` | 非 class 导出 |
| `MAX_ANCHOR_CANDIDATES` | `-` | 非 class 导出 |
| `MAX_ANCHOR_LENGTH` | `-` | 非 class 导出 |
| `MAX_FULL_FILE_PIN_CHARS` | `-` | 非 class 导出 |
| `MAX_PINNED_INJECTION_CHARS` | `-` | 非 class 导出 |
| `MAX_SESSION_PIN_COUNT` | `-` | 非 class 导出 |
| `NEARBY_PIN_SEARCH_WINDOW` | `-` | 非 class 导出 |
| `WorkspacePinExecutionContext` | `src/unchain/workspace/pins.py:35` | `../api/input-workspace-schemas.md#workspacepinexecutioncontext` |
| `build_pin_record` | `-` | 非 class 导出 |
| `build_pinned_prompt_messages` | `-` | 非 class 导出 |
| `find_duplicate_pin` | `-` | 非 class 导出 |
| `load_workspace_pins` | `-` | 非 class 导出 |
| `remove_pins` | `-` | 非 class 导出 |
| `resolve_pin` | `-` | 非 class 导出 |
| `save_workspace_pins` | `-` | 非 class 导出 |
