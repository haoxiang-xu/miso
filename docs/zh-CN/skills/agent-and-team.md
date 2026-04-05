# Agent 与 Team

`agent-and-team` 主题的正式简体中文 skills 章节。

## 角色与边界

本章记录高层编排表面：单 Agent 如何配置和运行、Team 如何协作，以及子代理如何复用同一执行模型。

## 依赖关系

- `Agent` 负责工具归一化、memory 强制转换和 `Broth` 构造。
- `Team` 每一步都委托给具名 `Agent`，自身只管理共享路由和打分状态。
- `ResponseFormat`、toolkit catalog 状态和 memory namespace 会贯穿 run/resume 表面。

## 核心对象

- `Agent`
- `Team`
- `_SubagentConfig`
- `_SubagentCounters`
- `_SubagentRuntime`

## 执行流与状态流

- `Agent.run()` 合并工具、创建新的 runtime，并执行到完成或暂停。
- `Agent.resume_human_input()` 恢复挂起的 catalog/runtime 状态并继续同一会话。
- `Team.run()` 发布初始 envelope、给待处理工作打分，然后让 agent 发布/handoff/finalize，直到静默或完成。

## 配置面

- Agent identity/instructions/provider/model。
- 短期和长期 memory 配置。
- 子代理限制、toolkit catalog 设置以及 `max_iterations` 之类的每次运行覆盖项。

## 扩展点

- 通过 `Agent.as_tool()` 把 agent 暴露为工具。
- 通过 `enable_subagents()` 开启嵌套代理委托。
- 用 callback 从 solo/team 运行中流式接收事件。

## 常见陷阱

- `Team` 会强制 agent 名称唯一并要求 owner 有效。
- 子代理深度与子数量计数用于防止失控递归。
- 暂停后的恢复必须使用上一轮返回的 continuation。

## 关联 class 参考

- [Agents API](../api/agents.md)
- [Runtime API](../api/runtime.md)
- [Memory API](../api/memory.md)

## 源码入口

- `src/unchain/agent/agent.py`
- `src/unchain/agent/team.py`

## Agent 实践

`Agent` 是面向调用方的高层接口。它负责保存 agent 的身份、默认指令、工具集合、memory 配置以及每次运行的默认覆盖项；但它自己并不直接执行 provider loop，而是在每次 `run()` 时创建新的 `Broth` 去完成底层执行。

换句话说，`Agent` 是配置容器和公共入口表面，而 `Broth` 是单次运行的执行器。`Agent` 定义了这个 agent 是什么以及它携带什么默认设置；`Broth` 定义了某次具体请求如何执行。

## 当前执行流

1. `Agent.run()` 把字符串或消息列表规范化成 conversation，并在前面拼上 agent 的 `instructions` 和任何额外 system message。
2. 它合并默认 payload、response format 和每次运行的覆盖项，并判断是否需要为本次运行启用子代理运行时。
3. `_build_engine()` 创建新的 `Broth`，挂载 provider 配置、api key、memory manager、toolkit catalog 配置以及 agent 的合并工具集。
4. 组装好的 messages 和运行时选项被转发到 `engine.run()`。若执行因人类输入而暂停，`Agent.resume_human_input()` 会创建新的 runtime、恢复 continuation 状态并继续同一会话。
5. 启用 toolkit catalog 时，`Agent` 还会在暂停前后捕获和恢复 catalog state token，确保恢复后的 runtime 看到相同的 active 和 managed toolkit。

## 设计说明

这种分层让 `Agent` 保持为稳定的用户 API，而把 provider 适配、tool loop、暂停逻辑和 token 计数集中到 `Broth`。实际收益包括：每次运行使用新 runtime、显式的 continuation 和 memory 状态流、以及更简单的心智模型 -- 调用方只需理解 `Agent.run()` 和 `Agent.resume_human_input()`。

## 详细的遗留参考

以下保留了原始仓库 skill 笔记，用于延续性与额外示例。规范副本现已迁入此文档树。

> 高层 `Agent` API、`Team` 多 agent 协调、子代理启用、namespace 隔离以及 callback 集成。

## Agent

`Agent` 是首要的高层接口。它持有工具、memory 配置和运行时选项，并在每次 `run()` 时创建新的 `Broth` 引擎。

### 构造

```python
from unchain import Agent
from unchain.toolkits import CodeToolkit
from unchain.memory import MemoryConfig

agent = Agent(
    name="coder",                            # Agent ���份
    instructions="You are a code assistant.", # 系统提示
    provider="openai",                       # LLM provider
    model="gpt-5",                           # 模型标识
    api_key=None,                            # 为 None 时使用环境变量
    tools=[                                  # Tool、Toolkit 或 callable
        CodeToolkit(workspace_root="."),
    ],
    short_term_memory=MemoryConfig(last_n_turns=10),
    long_term_memory=None,                   # LongTermMemoryConfig 或 dict
    defaults={"on_tool_confirm": my_handler},
    broth_options={"max_iterations": 8},
)
```

### `tools` 参数的灵活性

`tools` 列表接受混合类型：

```python
tools=[
    CodeToolkit(workspace_root="."),       # Toolkit 实例 → 其所有工具
    my_tool,                               # Tool 对象 → 单个��具
    my_function,                           # Callable → 自动包裹为 Tool
]
```

所有工具在传递给 `Broth` 之前会合并为单个 `Toolkit`。

### 运行

```python
messages, bundle = agent.run(
    messages="Inspect the repo.",            # str 或 list[dict]
    session_id=None,                         # 为 None 时自动生成 UUID
    memory_namespace=None,                   # 默认与 session_id 相同
    max_iterations=None,                     # 覆盖 broth_options
    payload=None,                            # 透传 dict，用于上下文
    callback=None,                           # 事件 callback 函数
)
```

**返回值：**

```python
messages  # list[dict] -- 完整对话 (system + user + assistant + tool 消息)
bundle    # dict -- 元数据:
          #   consumed_tokens: int
          #   stop_reason: str ("complete" | "max_iterations" | "human_input" | ...)
          #   artifacts: list
          #   toolkit_catalog_token: str | None
```

### 暂停后恢复

当 Broth 暂停时 (确认、人类输入)：

```python
# 首次运行暂停
messages, bundle = agent.run("Do something risky.")
# bundle["stop_reason"] == "human_input"

# 用户提供响应
messages, bundle = agent.resume_human_input(
    response=ToolConfirmationResponse(approved=True),
    # 或 HumanInputResponse (用于 ask_user)
)
```

## Toolkit Catalog

启用运行时动态 toolkit 激活/停用：

```python
agent.enable_toolkit_catalog(
    managed_toolkit_ids=["code", "external_api"],
    always_active_toolkit_ids=["code"],  # 不可被停用
)
```

这会注入 5 个 catalog 管理工具。LLM 在运行期间可按需激活/停用 toolkit。always-active 的 toolkit 不能被停用。

**状态保存**: catalog 状态通过 `bundle["toolkit_catalog_token"]` 中存储的 state token 跨 `run()` 暂停保存。

## 子代理 (Subagents)

启用 agent 动态生成子 agent：

```python
agent.enable_subagents(
    tool_name="spawn_subagent",   # 暴露给 LLM 的工具名
    max_depth=6,                   # 最大嵌套深度
    max_total_agents=20,           # 最大总 agent 数
    child_tools=[...],             # 子 agent 可用的工具 (默认继承父级)
)
```

### 工作原理

1. LLM 调用 `spawn_subagent(name, role, task)`
2. 框架创建子 `Agent`，具备：
   - 继承的工具和 memory 配置
   - 以 role 和 depth context 覆盖的系统提示
   - 隔离的 memory namespace: `{parent_namespace}:{child_name}`
3. 子 agent 运行至完成
4. 结果作为工具结果返回给父 agent

### 约束

- 深度追踪防止无限递归
- 总 agent 计数防止资源耗尽
- 每个子 agent 有自己的 `session_id` 和 `memory_namespace`

## Team

`Team` 通过基于频道的异步消息来协调多个 agent。

### 构造

```python
from unchain import Agent, Team

analyst = Agent(name="analyst", provider="openai", model="gpt-5", instructions="...")
coder = Agent(name="coder", provider="openai", model="gpt-5", instructions="...")

team = Team(
    agents=[analyst, coder],
    channels={
        "main": {"subscribers": ["analyst", "coder"]},
        "code_review": {"subscribers": ["coder"]},
    },
    owner="analyst",              # 能终结 team 运行的 agent
    max_steps=20,                 # 所有 agent 的最大总轮次
)
```

### 执行

```python
result = team.run(
    messages="Build a web scraper.",
    session_id=None,
    callback=None,
)
```

**返回值** (与 `Agent.run()` 不同)：

```python
result = {
    "transcript": [...],       # 所有 agent 消息的有序列表
    "events": [...],           # 事件日志 (scheduled, handoff, idle, finalized)
    "stop_reason": str,        # "quiescent" | "finalized" | "max_steps"
    "agent_bundles": {...},    # 每个 agent 的 bundle dict
}
```

### Agent 选择打分

当多个 agent 都可能作为下一个行动者时，Team 使用打分系统：

| 信号         | 分值     | 说明                                      |
| ------------ | -------- | ----------------------------------------- |
| Handoff      | 3        | Agent A 显式交接给 Agent B                |
| Mention      | 2        | Agent A 在消息中提到 @AgentB              |
| User input   | 1        | 初始用户消息投递到频道                    |
| Owner bonus  | +0.5     | owner agent 的平局加分                    |
| Alphabetical | 平局决胜 | 按名称字母序最终决胜                      |

得分最高的 agent 下一个行动。

### 基于频道的通信

```text
User: "Build a web scraper."
  ↓ 发布到 "main" 频道

analyst (订阅 "main") → 得分最高 → 运行
  ↓ 向 "main" 发布响应
  ↓ 通过 "main" 向 "coder" handoff

coder (订阅 "main") → 得分 3 (handoff) → 运行
  ↓ 向 "main" 发布代码
  ↓ 提到 @analyst

analyst (订阅 "main") → 得分 2 (mention) → 运行
  ↓ 发布 "Looks good" + finalize

Team 停止 (stop_reason="finalized")
```

### Handoff

Agent 可以显式转移控制：

```text
Agent response: "Handing off to @coder for implementation."
```

框架检测到 handoff 模式后，给 coder 加 3 分。

### 终结 (Finalization)

只有 **owner** 才能终结 (结束 team 运行)。非 owner agent 试图终结时会收到错误，team 继续运行。

```text
analyst (owner): "All tasks complete. [FINALIZE]"
→ Team 停止，stop_reason="finalized"
```

### 停止条件

| 条件            | `stop_reason` | 说明                            |
| --------------- | ------------- | ------------------------------- |
| Owner 终结      | `"finalized"` | Owner agent 发出完成信号        |
| 无 agent 得分   | `"quiescent"` | 所有 agent 空闲，无待处理工作   |
| 达到步数上限    | `"max_steps"` | 总轮次达到 `max_steps`          |

## Memory Namespace 隔离

| 场景       | 模式                              | 示例                   |
| ---------- | --------------------------------- | ---------------------- |
| 单 agent   | `session_id`                      | `abc-123`              |
| Team agent | `{session_id}:{agent_name}`       | `abc-123:coder`        |
| 子代理     | `{parent_namespace}:{child_name}` | `abc-123:coder:helper` |

这确保每个 agent 的长期 memory 彼此隔离，同时仍允许共享 session store。

## Callback 集成

`Agent.run()` 和 `Team.run()` 都接受 callback：

```python
def my_callback(event: dict) -> None:
    match event["type"]:
        case "message_published":
            print(f"[{event.get('agent', 'system')}] {event['data']}")
        case "tool_result":
            print(f"  Tool: {event['tool_name']} → {event['result']}")
        case "handoff":
            print(f"  Handoff: {event['from']} → {event['to']}")
```

Team 额外增加以下事件类型：

| 事件类型         | 触发时机                   |
| ---------------- | -------------------------- |
| `scheduled`      | Agent 被选为下一个行动者   |
| `handoff`        | Agent 向另一个 agent 交接  |
| `idle`           | Agent 无事可做             |
| `finalized`      | Owner 结束 team 运行       |
| `step_completed` | 一个 agent 轮次结束        |

## 常见陷阱

1. **`Agent.run()` 返回 `(messages, bundle)`；`Team.run()` 返回 dict** -- 两者形状不同。不要把 team 结果当作元组来解包。

2. **每次运行使用新 Broth** -- Agent 状态 (工具、memory 配置) 会持久化，但运行时引擎会重新创建。除非配置了 memory，否则运行之间不会泄漏会话状态。

3. **finalization 需要 owner** -- 如果 Team 未设置 `owner`，则没有 agent 能 finalize，team 会一直运行到 `max_steps` 或 quiescence。

4. **子代理 namespace 会累积** -- 子代理的子代理会得到 namespace `root:parent:child`。深层嵌套会产生很长的 namespace 字符串。

5. **`max_steps` 是总轮次，不是每个 agent 的** -- 一个有 3 个 agent 且 `max_steps=20` 的 team，平均每个 agent 约 6-7 轮。

6. **catalog 状态必须跨暂停传递** -- 如果 agent 有 toolkit catalog 且运行暂停了，bundle 中的 `toolkit_catalog_token` 必须保存用于恢复。

7. **Team 中的 agent 默认不共享任何东西** -- 每个 agent 有自己的工具、memory 和指令。通信只通过频道进行。

## 相关 Skills

- [architecture-overview.md](architecture-overview.md) -- 系统级组件关系
- [runtime-engine.md](runtime-engine.md) -- Broth 在 Agent 下如何执行
- [memory-system.md](memory-system.md) -- Memory namespace 约定
- [tool-system-patterns.md](tool-system-patterns.md) -- Agent 的工具注册
