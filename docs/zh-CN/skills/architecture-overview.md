# 架构总览

`architecture-overview` 主题的正式简体中文 skills 章节。

## 角色与边界

本章解释包的分层、哪些模块是基础层，以及请求和数据如何从用户代码进入运行时循环再返回调用方。

## 依赖关系

- `unchain.tools` 是基础层，依赖最少。
- `unchain.runtime` 建立在 tools、memory、workspace、input、schema 之上。
- `unchain.agents` 负责编排 `Broth`、memory 和 toolkits，但不会反向污染底层依赖。

## 核心对象

- `Agent` 与 `Team` 作为编排入口。
- `Broth` 作为工具调用引擎。
- `Tool`、`Toolkit`、`ToolkitRegistry`、`ToolkitCatalogRuntime` 作为工具层。
- `MemoryManager` 及其 stores/strategies 作为上下文基础设施。

## 执行流与状态流

- 用户代码构造 `Agent` 或 `Broth`。
- 运行时规范化工具、准备 memory、注入 pinned context，并发起 provider 调用。
- 工具调用会被执行，或因为确认/人类输入而暂停。
- 会话状态、artifacts 和 memory 写入会在结束或暂停前提交。

## 配置面

- provider/model/api key。
- memory 配置与长期适配器。
- toolkit catalog 与受管 toolkit 列表。

## 扩展点

- 在 `runtime/providers/` 新增 provider。
- 通过 toolkit manifest 添加 builtin 或 plugin。
- 替换 memory store/adapter，而不改变高层编排 API。

## 常见陷阱

- 顶层 API 故意很小，细节主要下沉到子包。
- 每次 `Agent.run()` 都会创建新的 `Broth`。
- catalog 激活是运行时状态，不是 import 时注册。

## 关联 class 参考

- [Agents API](../api/agents.md)
- [Runtime API](../api/runtime.md)
- [Tool System API](../api/tools.md)
- [Memory API](../api/memory.md)

## 源码入口

- `src/unchain/__init__.py`
- `src/unchain/agent/`
- `src/unchain/runtime/`
- `src/unchain/tools/`

## 详细的遗留参考

以下保留了原始仓库 skill 笔记，用于延续性与额外示例。规范副本现已迁入此文档树。

> unchain agent 框架的模块地图、依赖关系图和数据流。

## 包布局

```text
src/unchain/
├── __init__.py          # 公共 API: Agent, Team
├── agent/               # 高层 Agent 与 Team
│   ├── agent.py         #   Agent -- 单 agent 编排
│   └── team.py          #   Team -- 多 agent 频道协调
├── runtime/             # 底层 Broth 引擎 + provider
│   ├── engine.py        #   Broth -- 工具调用循环、memory、callback
│   ├── payloads.py      #   Provider 默认值 + 模型能力注册表
│   ├── files.py         #   OpenAI 文件上传辅助
│   ├── providers/       #   懒加载 provider SDK (openai, anthropic, gemini, ollama)
│   └── resources/       #   模型默认值和能力的 JSON 配置
├── tools/               # 工具原语与发现
│   ├── tool.py          #   Tool -- 包裹了 callable 和元数据
│   ├── toolkit.py       #   Toolkit -- Tool 的 dict 容器
│   ├── decorators.py    #   @tool 装饰器
│   ├── models.py        #   ToolParameter、确认类型、history optimizer
│   ├── registry.py      #   ToolkitRegistry -- 从三种来源发现 toolkit
│   ├── catalog.py       #   ToolkitCatalogRuntime -- 运行时动态激活/停用
│   └── confirmation.py  #   ToolConfirmationRequest / Response
├── toolkits/            # 内置 + MCP toolkit
│   ├── base.py          #   BuiltinToolkit -- workspace 安全的基类
│   ├── mcp.py           #   MCPToolkit -- MCP server 桥接
│   └── builtin/         #   预构建 toolkit (workspace, terminal, ask_user, external_api)
├── memory/              # 短期与长期 memory
│   ├── manager.py       #   MemoryManager -- 协调 store + strategy
│   ├── config.py        #   MemoryConfig / LongTermMemoryConfig 数据类
│   ├── strategies.py    #   上下文窗口策略 (LastNTurns, Summary, Hybrid)
│   ├── stores.py        #   SessionStore, VectorStoreAdapter 接口
│   ├── long_term.py     #   LongTermExtractor, profile store
│   ├── qdrant.py        #   Qdrant 向量数据库适配器
│   └── tool_history.py  #   工具调用历史压缩
├── input/               # 人类交互
│   ├── human_input.py   #   HumanInputRequest / Response, 结构化选择器
│   └── media.py         #   媒体上传工具
├── workspace/           # 会话级 pin
│   └── pins.py          #   锚点自适应的文件 pin 系统
├── schemas/             # 结构化输出
│   └── response.py      #   ResponseFormat -- JSON schema 输出
└── _internal/           # 私有辅助
    └── agent_shared.py  #   as_text(), normalize_mentions()
```

## 导入层级

依赖方向 **从上往下** -- 上层引用下层，绝不反向。

```text
Layer 0  (公共 API)       unchain              → 导出 Agent, Team
Layer 1  (编排层)         unchain.agent        → 引用 runtime, tools, toolkits, memory
Layer 2  (引擎层)         unchain.runtime      → 引用 tools, memory, workspace, input, schemas
Layer 3  (工具系统)       unchain.tools        → 不引用 unchain 任何内部模块 (完全自包含)
Layer 3  (toolkit 实现)   unchain.toolkits     → 引用 tools, workspace
Layer 3  (memory)         unchain.memory       → 引用 runtime (用于摘要调用), tools
Layer 4  (基础原语)       unchain.input, unchain.workspace, unchain.schemas, unchain._internal
```

**规则**: `unchain.tools` 是地基 -- 它拥有 **零内部依赖**。所有其他模块都建立在它之上。

## 数据流: 请求 → 响应

```text
用户代码
  │
  ▼
Agent.run(messages, session_id, ...)
  │  1. 把所有已注册工具合并成一个 Toolkit
  │  2. 创建新的 Broth runtime 引擎
  │  3. 挂载 MemoryManager (若已配置)
  │  4. 调用 broth.run(messages, toolkit, ...)
  │
  ▼
Broth.run()  --- 主执行循环 ---
  │
  │  for each iteration (至多 max_iterations 轮):
  │    ┌─────────────────────────────────────────┐
  │    │ 1. memory.prepare_messages()            │
  │    │    - 注入 workspace pin context          │
  │    │    - 应用上下文窗口策略                    │
  │    │                                          │
  │    │ 2. _fetch_once(messages, tools, ...)    │
  │    │    - 分发到 provider SDK                  │
  │    │    - 接收 assistant message + 调用列表    │
  │    │                                          │
  │    │ 3. for each tool_call:                  │
  │    │    - 确认门 (若需要)                      │
  │    │    - toolkit.execute(name, args)         │
  │    │    - 若 observe 则注入观察结果            │
  │    │                                          │
  │    │ 4. memory.commit_messages()             │
  │    │    - 存储本轮对话                         │
  │    │    - 提取长期事实                         │
  │    │                                          │
  │    │ 5. check: 无 tool_calls? → break         │
  │    └─────────────────────────────────────────┘
  │
  ▼
返回 (messages, bundle)
  │  bundle 包含: consumed_tokens, artifacts, stop_reason, ...
  │
  ▼
回到 Agent.run() → 返回给用户代码
```

## 组件关系

| 组件                    | 依赖                                                         | 被依赖                           |
| ----------------------- | ------------------------------------------------------------ | -------------------------------- |
| `Tool` / `Toolkit`      | — (自包含)                                                    | 所有组件                         |
| `BuiltinToolkit`        | `Toolkit`, `workspace.pins`                                  | 内置 toolkit 实现                |
| `ToolkitRegistry`       | `Toolkit`, 文件系统                                          | `Agent`, `ToolkitCatalogRuntime` |
| `ToolkitCatalogRuntime` | `ToolkitRegistry`, `Toolkit`                                 | `Agent`, `Broth`                 |
| `MemoryManager`         | `SessionStore`, context strategy, `Broth` (用于摘要)         | `Broth`                          |
| `Broth`                 | `Toolkit`, `MemoryManager`, provider, `ResponseFormat`       | `Agent`                          |
| `Agent`                 | `Broth`, `Toolkit`, `MemoryManager`, `ToolkitCatalogRuntime` | `Team`, 用户代码                 |
| `Team`                  | `Agent`                                                      | 用户代码                         |

## 关键设计原则

1. **最小公共表面** -- 只有 `Agent` 和 `Team` 是顶层导出。其余内容均从子包导入。

2. **每次运行使用新引擎** -- `Agent.run()` 每次调用都会创建新的 `Broth` 实例。运行之间不会残留状态 (memory 已外置)。

3. **工具即数据** -- `Tool` 就是元数据加一个 callable。参数 schema 从 Python 类型提示和 docstring 自动推断。

4. **三种 toolkit 发现来源** -- Builtin (随 unchain 发布)、Local (用户目录)、Plugin (entry points)。三者均使用相同的 `toolkit.toml` manifest。

5. **memory 可选且分层** -- 短期上下文策略和长期向量持久化可独立配置。

6. **provider 无关的核心** -- `Broth` 引擎使用规范消息格式。provider 特定的投影仅发生在边界层。

## 相关 Skills

- [creating-builtin-toolkits.md](creating-builtin-toolkits.md) -- 如何新增内置 toolkit
- [tool-system-patterns.md](tool-system-patterns.md) -- 工具定义与注册模式
- [memory-system.md](memory-system.md) -- memory 层级与配置
- [runtime-engine.md](runtime-engine.md) -- Broth 执行循环详解
- [agent-and-team.md](agent-and-team.md) -- Agent/Team 高层 API
- [testing-conventions.md](testing-conventions.md) -- 测试模式与 eval 框架
