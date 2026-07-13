# 类索引

`src/unchain` 下全部生产类，按参考页分组。

## [Agent API 参考](../api/agents.md)

- `Agent` -> `src/unchain/agent/agent.py:14` (top-level, class)

## [Kernel 类型](../api/runtime.md)

- `ToolCall` -> `src/unchain/kernel/types.py:8` (subpackage, dataclass)
- `TokenUsage` -> `src/unchain/kernel/types.py:15` (internal, dataclass)
- `ModelTurnResult` -> `src/unchain/kernel/types.py:22` (subpackage, dataclass)
- `KernelRunResult` -> `src/unchain/kernel/types.py:36` (subpackage, dataclass)
- `CompletionEvaluation` -> `src/unchain/runtime/completion.py:13` (subpackage, dataclass)
- `CompletionPolicy` -> `src/unchain/runtime/completion.py:24` (subpackage, dataclass)
- `CompletionPolicyRunner` -> `src/unchain/runtime/completion.py:47` (subpackage, dataclass)
- `ToolExecutionOutcome` -> `src/unchain/tools/confirmation.py:21` (subpackage, dataclass)

## [Interaction API 参考](../api/interaction.md)

- `InteractionError` -> `src/unchain/interaction/durable.py:36` (subpackage, error class)
- `InteractionIntegrityError` -> `src/unchain/interaction/durable.py:42` (subpackage, error class)
- `InteractionNotPendingError` -> `src/unchain/interaction/durable.py:48` (subpackage, error class)
- `InteractionReceiptConflictError` -> `src/unchain/interaction/durable.py:54` (subpackage, error class)
- `InteractionAlreadyAppliedError` -> `src/unchain/interaction/durable.py:60` (subpackage, error class)
- `InteractionRequest` -> `src/unchain/interaction/durable.py:214` (subpackage, frozen dataclass)
- `InteractionReceipt` -> `src/unchain/interaction/durable.py:407` (subpackage, frozen dataclass)
- `DurableInteractionSnapshot` -> `src/unchain/interaction/runtime.py:160` (module-only, frozen dataclass)
- `DurableInteractionRuntime` -> `src/unchain/interaction/runtime.py:205` (module-only, dataclass)
- `DurableMaxBudgetCallbackAdapter` -> `src/unchain/interaction/adapters.py:31` (module-only, dataclass)

## [工具系统 API 参考](../api/tools.md)

- `ToolkitCatalogConfig` -> `src/unchain/tools/catalog.py:34` (subpackage, dataclass)
- `ToolkitCatalogRuntime` -> `src/unchain/tools/catalog.py:76` (subpackage, class)
- `ToolParameter` -> `src/unchain/tools/models.py:155` (subpackage, dataclass)
- `ToolHistoryOptimizationContext` -> `src/unchain/tools/models.py:178` (subpackage, dataclass)
- `ToolExecutionContext` -> `src/unchain/tools/models.py:191` (subpackage, dataclass)
- `ToolConfirmationPolicy` -> `src/unchain/tools/models.py:203` (subpackage, dataclass)
- `NormalizedToolHistoryRecord` -> `src/unchain/tools/models.py:228` (subpackage, dataclass)
- `ToolConfirmationRequest` -> `src/unchain/tools/models.py:246` (subpackage, dataclass)
- `ToolConfirmationResponse` -> `src/unchain/tools/models.py:273` (subpackage, dataclass)
- `ToolRegistryConfig` -> `src/unchain/tools/registry.py:192` (subpackage, dataclass)
- `ToolDescriptor` -> `src/unchain/tools/registry.py:222` (subpackage, dataclass)
- `IconDescriptor` -> `src/unchain/tools/registry.py:246` (internal, dataclass)
- `ToolkitDescriptor` -> `src/unchain/tools/registry.py:286` (subpackage, dataclass)
- `ToolkitRegistry` -> `src/unchain/tools/registry.py:378` (subpackage, class)
- `Tool` -> `src/unchain/tools/tool.py:18` (subpackage, class)
- `Toolkit` -> `src/unchain/tools/toolkit.py:9` (subpackage, class)

## [Toolkit 实现参考](../api/toolkits.md)

- `BuiltinToolkit` -> `src/unchain/toolkits/base.py:14` (subpackage, class)
- `AgentReachToolkit` -> `src/unchain/toolkits/builtin/agent_reach/agent_reach.py:13` (subpackage, class)
- `CoreToolkit` -> `src/unchain/toolkits/builtin/core/core.py:30` (subpackage, class)
- `_PlanStep` -> `src/unchain/toolkits/builtin/plan/plan.py:30` (internal, dataclass)
- `_PlanState` -> `src/unchain/toolkits/builtin/plan/plan.py:59` (internal, dataclass)
- `PlanToolkit` -> `src/unchain/toolkits/builtin/plan/plan.py:192` (subpackage, class)
- `MCPToolkit` -> `src/unchain/toolkits/mcp.py:62` (subpackage, class)

## [Memory API 参考](../api/memory.md)

- `SessionStore` -> `src/unchain/memory/manager.py:21` (subpackage, protocol)
- `VectorStoreAdapter` -> `src/unchain/memory/manager.py:30` (subpackage, protocol)
- `LongTermProfileStore` -> `src/unchain/memory/manager.py:52` (subpackage, protocol)
- `LongTermVectorAdapter` -> `src/unchain/memory/manager.py:61` (subpackage, protocol)
- `ContextStrategy` -> `src/unchain/memory/manager.py:84` (subpackage, protocol)
- `ExecutionCheckpointHarness` -> `src/unchain/memory/checkpoint.py` (subpackage, class)
- `ExecutionCheckpointError` 及其 typed subclasses -> `src/unchain/memory/checkpoint_state.py` (subpackage, classes)
- `RevisionedSessionStore` -> `src/unchain/memory/revision.py` (subpackage, protocol)
- `SessionSnapshot` -> `src/unchain/memory/revision.py` (subpackage, dataclass)
- `SessionRevisionConflictError` 与 `SessionStoreCorruptionError` -> `src/unchain/memory/revision.py` (subpackage, classes)
- `InMemorySessionStore` -> `src/unchain/memory/manager.py:104` (subpackage, class)
- `JsonFileLongTermProfileStore` -> `src/unchain/memory/manager.py:117` (subpackage, class)
- `LongTermMemoryConfig` -> `src/unchain/memory/manager.py:144` (subpackage, dataclass)
- `MemoryConfig` -> `src/unchain/memory/manager.py:167` (subpackage, dataclass)
- `MemoryCommitResult` -> `src/unchain/memory/manager.py` (subpackage, dataclass)
- `LastNTurnsStrategy` -> `src/unchain/memory/manager.py:1642` (subpackage, class)
- `SummaryTokenStrategy` -> `src/unchain/memory/manager.py:1675` (subpackage, class)
- `HybridContextStrategy` -> `src/unchain/memory/manager.py:1779` (subpackage, class)
- `MemoryManager` -> `src/unchain/memory/manager.py:1866` (subpackage, class)
- `SessionHistoryOwnershipError` -> `src/unchain/memory/ownership.py` (subpackage, class)
- `QdrantVectorAdapter` -> `src/unchain/memory/qdrant.py:198` (internal, class)
- `QdrantLongTermVectorAdapter` -> `src/unchain/memory/qdrant.py:311` (internal, class)
- `JsonFileSessionStore` -> `src/unchain/memory/qdrant.py:422` (internal, class)

## Optimizers

- `SlidingWindowOptimizer` -> `src/unchain/optimizers/sliding_window.py` (subpackage, class) — 基于 token 的上下文窗口截断
- `SlidingWindowOptimizerConfig` -> `src/unchain/optimizers/sliding_window.py` (subpackage, dataclass) — SlidingWindowOptimizer 配置

## [Input、Workspace 与 Schema 参考](../api/input-workspace-schemas.md)

- `HumanInputOption` -> `src/unchain/input/human_input.py:61` (subpackage, dataclass)
- `HumanInputRequest` -> `src/unchain/input/human_input.py:89` (subpackage, dataclass)
- `HumanInputResponse` -> `src/unchain/input/human_input.py:225` (subpackage, dataclass)
- `ResponseFormat` -> `src/unchain/schemas/response.py:7` (subpackage, class)
- `WorkspacePinExecutionContext` -> `src/unchain/workspace/pins.py:35` (subpackage, dataclass)
- `ParsedSyntaxTree` -> `src/unchain/workspace/syntax.py:215` (internal, dataclass)
- `DeclarationCandidate` -> `src/unchain/workspace/syntax.py:228` (internal, dataclass)
