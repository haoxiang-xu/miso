# Class Index

Every production class under `src/unchain` grouped by reference page.

## [Agents API Reference](../api/agents.md)

- `_SubagentConfig` -> `src/unchain/agents/agent.py:74` (internal, dataclass)
- `_SubagentCounters` -> `src/unchain/agents/agent.py:83` (internal, dataclass)
- `_SubagentRuntime` -> `src/unchain/agents/agent.py:89` (internal, dataclass)
- `Agent` -> `src/unchain/agents/agent.py:114` (top-level, class)
- `Team` -> `src/unchain/agents/team.py:11` (top-level, class)

## [Runtime API Reference](../api/runtime.md)

- `ToolCall` -> `src/unchain/runtime/engine.py:68` (subpackage, dataclass)
- `ProviderTurnResult` -> `src/unchain/runtime/engine.py:74` (subpackage, dataclass)
- `TokenUsage` -> `src/unchain/runtime/engine.py:86` (internal, dataclass)
- `ToolExecutionOutcome` -> `src/unchain/runtime/engine.py:93` (subpackage, dataclass)
- `Broth` -> `src/unchain/runtime/engine.py:103` (subpackage, class)

## [Tool System API Reference](../api/tools.md)

- `ToolkitCatalogConfig` -> `src/unchain/tools/catalog.py:34` (subpackage, dataclass)
- `ToolkitCatalogRuntime` -> `src/unchain/tools/catalog.py:76` (subpackage, class)
- `ToolParameter` -> `src/unchain/tools/models.py:155` (subpackage, dataclass)
- `ToolHistoryOptimizationContext` -> `src/unchain/tools/models.py:178` (subpackage, dataclass)
- `NormalizedToolHistoryRecord` -> `src/unchain/tools/models.py:191` (subpackage, dataclass)
- `ToolConfirmationRequest` -> `src/unchain/tools/models.py:209` (subpackage, dataclass)
- `ToolConfirmationResponse` -> `src/unchain/tools/models.py:233` (subpackage, dataclass)
- `ToolRegistryConfig` -> `src/unchain/tools/registry.py:192` (subpackage, dataclass)
- `ToolDescriptor` -> `src/unchain/tools/registry.py:222` (subpackage, dataclass)
- `IconDescriptor` -> `src/unchain/tools/registry.py:246` (internal, dataclass)
- `ToolkitDescriptor` -> `src/unchain/tools/registry.py:286` (subpackage, dataclass)
- `ToolkitRegistry` -> `src/unchain/tools/registry.py:378` (subpackage, class)
- `Tool` -> `src/unchain/tools/tool.py:16` (subpackage, class)
- `Toolkit` -> `src/unchain/tools/toolkit.py:9` (subpackage, class)

## [Toolkit Implementations Reference](../api/toolkits.md)

- `BuiltinToolkit` -> `src/unchain/toolkits/base.py:10` (subpackage, class)
- `AskUserToolkit` -> `src/unchain/toolkits/builtin/ask_user/ask_user.py:7` (subpackage, class)
- `ExternalAPIToolkit` -> `src/unchain/toolkits/builtin/external_api/external_api.py:12` (subpackage, class)
- `TerminalToolkit` -> `src/unchain/toolkits/builtin/terminal/terminal.py:10` (subpackage, class)
- `_TerminalSession` -> `src/unchain/toolkits/builtin/terminal_runtime.py:15` (internal, dataclass)
- `_TerminalRuntime` -> `src/unchain/toolkits/builtin/terminal_runtime.py:22` (internal, class)
- `WorkspaceToolkit` -> `src/unchain/toolkits/builtin/workspace/workspace.py:24` (subpackage, class)
- `MCPToolkit` -> `src/unchain/toolkits/mcp.py:62` (subpackage, class)

## [Memory API Reference](../api/memory.md)

- `SessionStore` -> `src/unchain/memory/manager.py:21` (subpackage, protocol)
- `VectorStoreAdapter` -> `src/unchain/memory/manager.py:30` (subpackage, protocol)
- `LongTermProfileStore` -> `src/unchain/memory/manager.py:52` (subpackage, protocol)
- `LongTermVectorAdapter` -> `src/unchain/memory/manager.py:61` (subpackage, protocol)
- `ContextStrategy` -> `src/unchain/memory/manager.py:84` (subpackage, protocol)
- `InMemorySessionStore` -> `src/unchain/memory/manager.py:104` (subpackage, class)
- `JsonFileLongTermProfileStore` -> `src/unchain/memory/manager.py:117` (subpackage, class)
- `LongTermMemoryConfig` -> `src/unchain/memory/manager.py:144` (subpackage, dataclass)
- `MemoryConfig` -> `src/unchain/memory/manager.py:167` (subpackage, dataclass)
- `LastNTurnsStrategy` -> `src/unchain/memory/manager.py:1642` (subpackage, class)
- `SummaryTokenStrategy` -> `src/unchain/memory/manager.py:1675` (subpackage, class)
- `HybridContextStrategy` -> `src/unchain/memory/manager.py:1779` (subpackage, class)
- `MemoryManager` -> `src/unchain/memory/manager.py:1866` (subpackage, class)
- `QdrantVectorAdapter` -> `src/unchain/memory/qdrant.py:198` (internal, class)
- `QdrantLongTermVectorAdapter` -> `src/unchain/memory/qdrant.py:305` (internal, class)
- `JsonFileSessionStore` -> `src/unchain/memory/qdrant.py:410` (internal, class)

## [Input, Workspace, and Schema Reference](../api/input-workspace-schemas.md)

- `HumanInputOption` -> `src/unchain/input/human_input.py:61` (subpackage, dataclass)
- `HumanInputRequest` -> `src/unchain/input/human_input.py:89` (subpackage, dataclass)
- `HumanInputResponse` -> `src/unchain/input/human_input.py:225` (subpackage, dataclass)
- `ResponseFormat` -> `src/unchain/schemas/response.py:7` (subpackage, class)
- `WorkspacePinExecutionContext` -> `src/unchain/workspace/pins.py:35` (subpackage, dataclass)
- `ParsedSyntaxTree` -> `src/unchain/workspace/syntax.py:215` (internal, dataclass)
- `DeclarationCandidate` -> `src/unchain/workspace/syntax.py:228` (internal, dataclass)
