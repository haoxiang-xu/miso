# 导出索引

各包 `__init__` 中声明的导出，以及对应的参考页位置。

## `src/unchain/__init__.py`

| 名称 | 源码 | 参考 |
| --- | --- | --- |
| `Agent` | `src/unchain/agent/agent.py:14` | `../api/agents.md#agent` |
| `__brand__` | `-` | 非 class 导出 |
| `__tagline__` | `-` | 非 class 导出 |
| `__version__` | `-` | 非 class 导出 |

## `src/unchain/agent/__init__.py`

| 名称 | 源码 | 参考 |
| --- | --- | --- |
| `Agent` | `src/unchain/agent/agent.py:14` | `../api/agents.md#agent` |
| `AgentBuilder` | `src/unchain/agent/builder.py:149` | `../api/agents.md#agentbuilder` |
| `AgentCallContext` | `src/unchain/agent/builder.py:21` | `../api/agents.md#agentcallcontext` |
| `AgentModule` | `src/unchain/agent/modules/base.py:10` | 非 class 导出 |
| `AgentSpec` | `src/unchain/agent/spec.py:8` | 非 class 导出 |
| `AgentState` | `src/unchain/agent/spec.py:19` | 非 class 导出 |
| `BaseAgentModule` | `src/unchain/agent/modules/base.py:18` | 非 class 导出 |
| `MemoryModule` | `src/unchain/agent/modules/memory.py:12` | 非 class 导出 |
| `ModelIOFactoryRegistry` | `src/unchain/agent/model_io.py:10` | 非 class 导出 |
| `OptimizersModule` | `src/unchain/agent/modules/optimizers.py:9` | 非 class 导出 |
| `PoliciesModule` | `src/unchain/agent/modules/policies.py:12` | 非 class 导出 |
| `PreparedAgent` | `src/unchain/agent/builder.py:44` | 非 class 导出 |
| `SubagentModule` | `src/unchain/agent/modules/subagents.py:17` | 非 class 导出 |
| `ToolsModule` | `src/unchain/agent/modules/tools.py:10` | 非 class 导出 |

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
| `BaseMemoryHarness` | `src/unchain/memory/base.py:89` | 非 class 导出 |
| `ContextStrategy` | `src/unchain/memory/manager.py:84` | `../api/memory.md#contextstrategy` |
| `HybridContextStrategy` | `src/unchain/memory/manager.py:1779` | `../api/memory.md#hybridcontextstrategy` |
| `InMemorySessionStore` | `src/unchain/memory/manager.py:104` | `../api/memory.md#inmemorysessionstore` |
| `JsonFileLongTermProfileStore` | `src/unchain/memory/manager.py:117` | `../api/memory.md#jsonfilelongtermprofilestore` |
| `KernelMemoryRuntime` | `src/unchain/memory/runtime.py:59` | 非 class 导出 |
| `LastNTurnsStrategy` | `src/unchain/memory/manager.py:1642` | `../api/memory.md#lastnturnsstrategy` |
| `LongTermExtractor` | `-` | 非 class 导出 |
| `LongTermMemoryConfig` | `src/unchain/memory/manager.py:144` | `../api/memory.md#longtermmemoryconfig` |
| `LongTermProfileStore` | `src/unchain/memory/manager.py:52` | `../api/memory.md#longtermprofilestore` |
| `LongTermRecallMemoryHarness` | `src/unchain/memory/recall_long_term.py:12` | 非 class 导出 |
| `LongTermVectorAdapter` | `src/unchain/memory/manager.py:61` | `../api/memory.md#longtermvectoradapter` |
| `MemoryBootstrapHarness` | `src/unchain/memory/bootstrap.py:10` | 非 class 导出 |
| `MemoryCommitHarness` | `src/unchain/memory/commit.py:10` | 非 class 导出 |
| `MemoryConfig` | `src/unchain/memory/manager.py:167` | `../api/memory.md#memoryconfig` |
| `MemoryContext` | `src/unchain/memory/base.py:14` | 非 class 导出 |
| `MemoryHarness` | `src/unchain/memory/base.py:84` | 非 class 导出 |
| `MemoryManager` | `src/unchain/memory/manager.py:1866` | `../api/memory.md#memorymanager` |
| `SessionStore` | `src/unchain/memory/manager.py:21` | `../api/memory.md#sessionstore` |
| `ShortTermRecallMemoryHarness` | `src/unchain/memory/short_term.py:12` | 非 class 导出 |
| `SummaryGenerator` | `-` | 非 class 导出 |
| `SummaryTokenStrategy` | `src/unchain/memory/manager.py:1675` | `../api/memory.md#summarytokenstrategy` |
| `VectorStoreAdapter` | `src/unchain/memory/manager.py:30` | `../api/memory.md#vectorstoreadapter` |

## `src/unchain/runtime/__init__.py`

| 名称 | 源码 | 参考 |
| --- | --- | --- |
| `DEFAULT_PAYLOADS_RESOURCE` | `-` | 非 class 导出 |
| `MODEL_CAPABILITIES_RESOURCE` | `-` | 非 class 导出 |
| `load_default_payloads` | `-` | 非 class 导出 |
| `load_model_capabilities` | `-` | 非 class 导出 |

## `src/unchain/schemas/__init__.py`

| 名称 | 源码 | 参考 |
| --- | --- | --- |
| `CLAUDE_HAIKU_35` | `-` | 非 class 导出 |
| `CLAUDE_SONNET_35` | `-` | 非 class 导出 |
| `GEMINI_PRO_15` | `-` | 非 class 导出 |
| `GPT_4O` | `-` | 非 class 导出 |
| `ModelCapabilities` | `src/unchain/schemas/models.py:8` | 非 class 导出 |
| `ModelConfiguration` | `src/unchain/schemas/models.py:73` | 非 class 导出 |
| `ModelDefaultPayload` | `src/unchain/schemas/models.py:57` | 非 class 导出 |
| `ResponseFormat` | `src/unchain/schemas/response.py:7` | `../api/input-workspace-schemas.md#responseformat` |

## `src/unchain/toolkits/__init__.py`

| 名称 | 源码 | 参考 |
| --- | --- | --- |
| `AskUserToolkit` | `src/unchain/toolkits/builtin/ask_user/ask_user.py:7` | `../api/toolkits.md#askusertoolkit` |
| `BuiltinToolkit` | `src/unchain/toolkits/base.py:14` | `../api/toolkits.md#builtintoolkit` |
| `CodeToolkit` | `src/unchain/toolkits/builtin/code/code.py:30` | `../api/toolkits.md#codetoolkit` |
| `ExternalAPIToolkit` | `src/unchain/toolkits/builtin/external_api/external_api.py:13` | `../api/toolkits.md#externalapitoolkit` |
| `MCPToolkit` | `src/unchain/toolkits/mcp.py:62` | `../api/toolkits.md#mcptoolkit` |

## `src/unchain/tools/__init__.py`

| 名称 | 源码 | 参考 |
| --- | --- | --- |
| `CATALOG_TOOL_NAMES` | `-` | 非 class 导出 |
| `HistoryPayloadOptimizer` | `-` | 非 class 导出 |
| `NormalizedToolHistoryRecord` | `src/unchain/tools/models.py:228` | `../api/tools.md#normalizedtoolhistoryrecord` |
| `TOOLKIT_ACTIVATE_TOOL_NAME` | `-` | 非 class 导出 |
| `TOOLKIT_DEACTIVATE_TOOL_NAME` | `-` | 非 class 导出 |
| `TOOLKIT_DESCRIBE_TOOL_NAME` | `-` | 非 class 导出 |
| `TOOLKIT_LIST_ACTIVE_TOOL_NAME` | `-` | 非 class 导出 |
| `TOOLKIT_LIST_TOOL_NAME` | `-` | 非 class 导出 |
| `Tool` | `src/unchain/tools/tool.py:18` | `../api/tools.md#tool` |
| `ToolConfirmationPolicy` | `src/unchain/tools/models.py:203` | `../api/tools.md#toolconfirmationpolicy` |
| `ToolConfirmationRequest` | `src/unchain/tools/models.py:246` | `../api/tools.md#toolconfirmationrequest` |
| `ToolConfirmationResponse` | `src/unchain/tools/models.py:273` | `../api/tools.md#toolconfirmationresponse` |
| `ToolContext` | `src/unchain/tools/base.py:16` | 非 class 导出 |
| `ToolDescriptor` | `src/unchain/tools/registry.py:222` | `../api/tools.md#tooldescriptor` |
| `ToolExecutionContext` | `src/unchain/tools/models.py:191` | `../api/tools.md#toolexecutioncontext` |
| `ToolExecutionOutcome` | `src/unchain/tools/confirmation.py:21` | `../api/tools.md#toolexecutionoutcome` |
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
