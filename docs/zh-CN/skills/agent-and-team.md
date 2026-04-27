# Agent 与 Subagents

`agent-and-team` 主题的正式简体中文 skills 章节。

> **状态说明**：旧版本里描述的独立 `Team` 类已经不在公共 API 里了。多 agent 协作现在用 **subagents**（delegate / handoff / worker batch 工具）表达，住在 `unchain.subagents` 包，通过 `SubagentModule` 配置。

## 角色与边界

本章描述高层编排表面：单个 `Agent` 怎么配置和运行，以及 subagents 怎么扩展同样的执行模型来做多 agent 工作。

## 依赖关系

- `Agent` 持有身份、指令、module 组合；每次调用通过 `AgentBuilder` 构造一个 `PreparedAgent`。
- `PreparedAgent` 包装装好的 `KernelLoop`、合并的 `Toolkit` 和已解析的 per-call 默认值。
- Subagent 工具（`delegate_to_subagent`、`handoff_to_subagent`、`spawn_worker_batch`）通过 `SubagentModule` 注册，由 `SubagentExecutor` 调度。

## 核心对象

- `Agent`
- `AgentBuilder` / `PreparedAgent` / `AgentCallContext`
- `AgentSpec` / `AgentState`
- Module 类型：`ToolsModule`、`MemoryModule`、`PoliciesModule`、`OptimizersModule`、`SubagentModule`、`ToolDiscoveryModule`
- `SubagentExecutor`、`SubagentPolicy`、`SubagentTemplate`（在 `unchain.subagents`）

## 执行流与状态流

- `Agent.run()` 规范化消息，构造 `AgentCallContext`，让每个 module 在新的 `AgentBuilder` 上配置自己，然后向 builder 要一个 `PreparedAgent`。
- `PreparedAgent.run()` 驱动 `KernelLoop.run()` 跑到完成或暂停，返回 `KernelRunResult`。
- `Agent.resume_human_input()` 在确认或人类输入暂停后，用 continuation payload 重新进入同一个 loop。
- `Agent.fork_for_subagent()` 创建子 `Agent`，叠加委派指令，可选地剥离 memory 做 ephemeral 子 run。
- `Agent.as_tool()` 把 agent 包成 `Tool`，可以塞进另一个 agent 的工具集里。

## 配置面

- Agent 身份：`name`、`instructions`、`provider`、`model`、`api_key`。
- Module 组合：`modules=(...)`。
- 允许列表过滤：`allowed_tools=(...)` 限定合并后的 toolkit。
- `Agent.run()` 的 per-call 覆盖：`max_iterations`、`payload`、`callback`、`on_tool_confirm`、`on_human_input`、`on_max_iterations`、`session_id`、`memory_namespace`、`tool_runtime_config`。

## 常见陷阱

- `Agent.run()` 返回单一 `KernelRunResult`（frozen dataclass），不是 `(messages, bundle)` 元组。
- 旧的 `Agent(tools=[...], short_term_memory=..., long_term_memory=..., broth_options=...)` 构造签名已经没了 —— 一切都通过 `modules=(...)` 传。
- `Team`、`enable_toolkit_catalog`、`enable_subagents` 已经不再是 runtime 方法；用 module 替代。
- Subagent 深度/子计数（用 `SubagentPolicy` 配）防止失控递归；由 `SubagentExecutor` 强制执行。
- 暂停后必须用上一次 `KernelRunResult` 里返回的 `continuation` 字段恢复。

## 关联 class 参考

- [Agents API](../api/agents.md)
- [Runtime API](../api/runtime.md)
- [Memory API](../api/memory.md)

## 源码入口

- `src/unchain/agent/agent.py`
- `src/unchain/agent/builder.py`
- `src/unchain/agent/modules/`
- `src/unchain/subagents/`

## Agent 实践

`Agent` 是面向调用方的高层接口。它持有身份、默认指令、module 集合，但自己并不执行模型 loop。每次 `run()` 都构造新的 `PreparedAgent`（带新的 `KernelLoop`）来真正执行。

换言之，`Agent` 是配置容器和公共入口，`KernelLoop` 是单次 run 的执行器。`Agent` 定义这个 agent 是什么、带什么默认值；`KernelLoop` 定义这次具体请求怎么执行。

## 构造

```python
from unchain import Agent
from unchain.agent import ToolsModule, MemoryModule, PoliciesModule
from unchain.toolkits import CoreToolkit
from unchain.memory import MemoryConfig

agent = Agent(
    name="coder",
    instructions="你是一个代码助手。",
    provider="openai",
    model="gpt-5",
    api_key=None,
    modules=(
        ToolsModule(tools=(CoreToolkit(workspace_root="."),)),
        MemoryModule(memory=MemoryConfig(last_n_turns=10)),
        PoliciesModule(max_iterations=8, on_tool_confirm=my_handler),
    ),
)
```

`ToolsModule` 的 `tools` 字段接受 `Toolkit`、`Tool` 或 callable 的混合 —— `AgentBuilder.build()` 时全部合并为一个 `Toolkit`。

## 运行

```python
result = agent.run(
    "Inspect the repo.",
    payload={},                              # 透传 dict 上下文
    callback=None,                           # 事件 callback
    max_iterations=None,                     # 覆盖 policies 默认
    session_id=None,                         # 不传则自动生成 UUID
    memory_namespace=None,                   # 不传则等于 session_id
    on_tool_confirm=None,                    # per-call 覆盖
)
```

返回值是 `KernelRunResult`（frozen dataclass），主要字段：

| 字段 | 说明 |
| --- | --- |
| `messages` | 完整对话（system + user + assistant + tool 消息）。 |
| `status` | run 结果（`"completed"`、`"awaiting_human_input"`、…）。 |
| `continuation` | 暂停时的 continuation payload，恢复时传回 `resume_human_input()`。 |
| `human_input_request` | 当 status 表示挂起时填充。 |
| `consumed_tokens` / `input_tokens` / `output_tokens` | 整个 run 的 token 计数。 |
| `last_turn_tokens` / `last_turn_input_tokens` / `last_turn_output_tokens` | 仅最后一轮的 token 计数。 |
| `iteration` | loop 跑了几轮。 |

## 暂停后恢复

kernel 因确认或 `ask_user_question` 暂停时：

```python
first = agent.run("Do something risky.")
# first.status == "awaiting_human_input"

# 用户给响应
final = agent.resume_human_input(
    conversation=first.messages,
    continuation=first.continuation,
    response={"approved": True},  # 或者 ask_user_question 的 HumanInputResponse
)
```

## Tool 暴露（module 驱动）

Toolkit catalog 和工具级 deferred discovery 都通过 module 接进来，不是 runtime 方法。完整说明见 [tool-system-patterns.md](tool-system-patterns.md)；速记：

```python
# Catalog 模式（toolkit 级懒加载）
from unchain.tools import ToolkitCatalogRuntime, ToolkitCatalogConfig
catalog = ToolkitCatalogRuntime(
    config=ToolkitCatalogConfig(
        managed_toolkit_ids=("code", "external_api"),
        always_active_toolkit_ids=("code",),
    ),
    eager_toolkits=[],
)
agent = Agent(name="...", modules=(ToolsModule(tools=(catalog,)),))

# Discovery 模式（工具级 deferred）
from unchain.agent import ToolDiscoveryModule
from unchain.tools import ToolDiscoveryConfig
agent = Agent(
    name="...",
    modules=(ToolDiscoveryModule(
        config=ToolDiscoveryConfig(managed_toolkit_ids=("code", "external_api")),
    ),),
)
```

## Subagents

Subagents 是由 LLM 调用的工具动态 spawn 的子 agent。通过 `SubagentModule` 配：

```python
from unchain.agent import SubagentModule
from unchain.subagents import SubagentPolicy, SubagentTemplate

agent = Agent(
    name="planner",
    instructions="...",
    modules=(
        SubagentModule(
            templates=(
                SubagentTemplate(
                    name="researcher",
                    instructions="研究问题并返回摘要。",
                    model="gpt-5",
                ),
            ),
            policy=SubagentPolicy(max_depth=6, max_total_agents=20),
        ),
    ),
)
```

module 默认注册三个工具：

| 工具 | 行为 |
| --- | --- |
| `delegate_to_subagent` | 给一个具名子 agent 派发任务；结果以 tool 消息返回。 |
| `handoff_to_subagent` | 把对话权交给另一个 agent（控制权转移）。 |
| `spawn_worker_batch` | 并行 fan out N 个 worker agent，结果以批返回。 |

### delegate run 怎么跑

1. LLM 调 `delegate_to_subagent(name=..., task=...)`。
2. `SubagentExecutor` 找到对应的 `SubagentTemplate`，调 `parent.fork_for_subagent(...)` 构造子 agent。
3. 子 agent 跑到结束（深度/子计数由 `SubagentPolicy` 强制）。
4. 结果作为该工具调用的结果返回给 parent。

### 约束

- `SubagentPolicy.max_depth` 防止无限递归。
- `SubagentPolicy.max_total_agents` 限制整棵树的资源使用。
- 每个子 agent 拿到自己的 `session_id` 和 `memory_namespace`。memory 策略（`"shared"`、`"ephemeral"` 等）决定子 agent 是否保留记忆。

## Agent 当作 Tool

把 agent 包成 `Tool` 塞进另一个 agent 的 toolkit：

```python
researcher_tool = researcher_agent.as_tool(
    name="research",
    description="研究一个问题并返回摘要。",
)
planner = Agent(
    name="planner",
    modules=(ToolsModule(tools=(researcher_tool,)),),
)
```

`as_tool()` 产出一个 `Tool`，它的 `execute()` 会用传入的参数调被包装 agent 的 `run()`。

## Memory namespace 隔离

| 场景       | 默认 `memory_namespace`               | 示例                  |
| ---------- | -------------------------------------- | --------------------- |
| 单 agent   | `session_id`                           | `abc-123`             |
| Subagent   | `{parent_namespace}:{subagent_name}`   | `abc-123:researcher`  |
| 嵌套 sub.  | `{root}:{parent}:{child}`（递归）      | `abc-123:planner:scout`|

这样既保证每个 agent 的长期记忆隔离，又允许共享 session store。

## Callback 集成

`Agent.run()` 接受 `callback`，会收到 kernel 与 harness 发出的所有事件（事件目录见 `runtime-engine.md`）：

```python
def my_callback(event: dict) -> None:
    match event["type"]:
        case "message_published":
            print(f"[{event.get('agent', 'system')}] {event['data']}")
        case "tool_result":
            print(f"  Tool: {event['tool_name']} → {event['result']}")
```

## 常见陷阱

1. **`Agent.run()` 返回 `KernelRunResult`，不是 `(messages, bundle)`** —— 在 dataclass 上访问 `.messages`、`.status`、`.continuation` 等。

2. **每次 run 都新 kernel** —— module 配置（tools、memory）保留在 `AgentSpec`，但 `KernelLoop` 每次重新构造。状态只通过 `MemoryModule` 跨 run。

3. **旧构造关键字已经没了** —— `tools=`、`short_term_memory=`、`long_term_memory=`、`broth_options=` 都不存在。用 module。

4. **Subagent namespace 会累加** —— sub 的 sub 拿到的 namespace 是 `root:parent:child`。深嵌套会产生很长的 namespace。

5. **Catalog/discovery 状态要跨暂停传** —— 两个 runtime 都通过 harness 的 `on_suspend` 阶段 checkpoint，但你仍然要把 `continuation` 传回 `resume_human_input()`。

6. **`Team` 不再是合适的原语** —— 需要多 agent 流程时，把它建模成一个 planner agent，用 `delegate_to_subagent` / `spawn_worker_batch` 调度 `SubagentTemplate`。

## 相关 skills

- [architecture-overview.md](architecture-overview.md) — 系统级组件关系
- [runtime-engine.md](runtime-engine.md) — `KernelLoop` 在 `Agent` 下怎么跑
- [memory-system.md](memory-system.md) — subagent 的 memory namespace 约定
- [tool-system-patterns.md](tool-system-patterns.md) — Tool 注册与暴露模式
