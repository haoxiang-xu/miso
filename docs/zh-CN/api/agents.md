# Agents API 参考

覆盖单 Agent 编排、Team 协作，以及用于受控子代理执行的内部运行时支架。

| 指标 | 值 |
| --- | --- |
| 类数量 | 5 |
| Dataclass | 3 |
| 协议 | 0 |
| 仅内部类型 | 3 |

## 覆盖地图

| 类 | 源码 | 导出 | 类型 |
| --- | --- | --- | --- |
| `_SubagentConfig` | `src/unchain/agents/agent.py:74` | internal | dataclass |
| `_SubagentCounters` | `src/unchain/agents/agent.py:83` | internal | dataclass |
| `_SubagentRuntime` | `src/unchain/agents/agent.py:89` | internal | dataclass |
| `Agent` | `src/unchain/agents/agent.py:114` | top-level | class |
| `Team` | `src/unchain/agents/team.py:11` | top-level | class |

### `src/unchain/agents/agent.py`

高层单 Agent 编排入口，负责 memory、toolkit 合并、暂停/恢复以及可选的子代理派生。

## _SubagentConfig

用于高层单 Agent 编排入口，负责 memory、toolkit 合并、暂停/恢复以及可选的子代理派生的 dataclass 载荷。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/unchain/agents/agent.py:74` |
| 模块职责 | 高层单 Agent 编排入口，负责 memory、toolkit 合并、暂停/恢复以及可选的子代理派生。 |
| 继承/协议 | `-` |
| 导出状态 | 未导出，应视为实现细节。 |
| 对象类型 | Dataclass；内部实现。 |

### 内部实现说明

Owned by `Agent` as the stored subagent configuration.

### 字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `tool_name` | `str` | 构造时必需。 |
| `description` | `str | None` | 构造时必需。 |
| `max_depth` | `int` | 构造时必需。 |
| `max_children_per_agent` | `int` | 构造时必需。 |
| `max_total_subagents` | `int` | 构造时必需。 |

### 公共方法

该类型除了 dataclass/protocol 结构外不暴露公共方法。

### 协作关系与关联类型

- `_SubagentCounters`
- `_SubagentRuntime`
- `Agent`

### 最小调用示例

```python
_SubagentConfig(tool_name=..., description=..., max_depth=..., max_children_per_agent=...)
```

## _SubagentCounters

用于高层单 Agent 编排入口，负责 memory、toolkit 合并、暂停/恢复以及可选的子代理派生的 dataclass 载荷。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/unchain/agents/agent.py:83` |
| 模块职责 | 高层单 Agent 编排入口，负责 memory、toolkit 合并、暂停/恢复以及可选的子代理派生。 |
| 继承/协议 | `-` |
| 导出状态 | 未导出，应视为实现细节。 |
| 对象类型 | Dataclass；内部实现。 |

### 内部实现说明

Owned by `Agent` subagent runtime bookkeeping.

### 字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `total_created` | `int` | 默认值：`0`。 |
| `direct_children` | `dict[tuple[str, ...], int]` | 默认值：`field(default_factory=dict)`。 |

### 公共方法

该类型除了 dataclass/protocol 结构外不暴露公共方法。

### 协作关系与关联类型

- `_SubagentConfig`
- `_SubagentRuntime`
- `Agent`

### 最小调用示例

```python
_SubagentCounters(total_created=..., direct_children=...)
```

## _SubagentRuntime

用于高层单 Agent 编排入口，负责 memory、toolkit 合并、暂停/恢复以及可选的子代理派生的 dataclass 载荷。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/unchain/agents/agent.py:89` |
| 模块职责 | 高层单 Agent 编排入口，负责 memory、toolkit 合并、暂停/恢复以及可选的子代理派生。 |
| 继承/协议 | `-` |
| 导出状态 | 未导出，应视为实现细节。 |
| 对象类型 | Dataclass；内部实现。 |

### 内部实现说明

Created inside `Agent` subagent execution paths and passed through nested runs.

### 字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `config` | `_SubagentConfig` | 构造时必需。 |
| `current_depth` | `int` | 构造时必需。 |
| `lineage` | `tuple[str, ...]` | 构造时必需。 |
| `counters` | `_SubagentCounters` | 构造时必需。 |
| `current_session_id` | `str` | 构造时必需。 |
| `current_memory_namespace` | `str` | 构造时必需。 |
| `payload` | `dict[str, Any] | None` | 构造时必需。 |
| `callback` | `Callable[[dict[str, Any]], None] | None` | 构造时必需。 |
| `max_iterations` | `int | None` | 构造时必需。 |
| `verbose` | `bool` | 构造时必需。 |
| `on_tool_confirm` | `Callable | None` | 构造时必需。 |
| `on_continuation_request` | `Callable | None` | 构造时必需。 |

### 公共方法

该类型除了 dataclass/protocol 结构外不暴露公共方法。

### 协作关系与关联类型

- `_SubagentConfig`
- `_SubagentCounters`
- `Agent`

### 最小调用示例

```python
_SubagentRuntime(config=..., current_depth=..., lineage=..., counters=...)
```

## Agent

高层单 Agent 门面对象，负责持有配置、归一化 tools、创建新的 runtime，并暴露 run/resume/step/as-tool 入口。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/unchain/agents/agent.py:114` |
| 模块职责 | 高层单 Agent 编排入口，负责 memory、toolkit 合并、暂停/恢复以及可选的子代理派生。 |
| 继承/协议 | `-` |
| 导出状态 | 通过 `unchain` 顶层导出。 |
| 对象类型 | 类；公开或包内可见。 |

### 构造表面

该类主要通过构造函数定义必需输入和校验逻辑。

- `__init__(self, *, name: str, instructions: str='', provider: str='openai', model: str='gpt-5', api_key: str | None=None, tools: list[Tool | Toolkit | Callable[..., Any]] | None=None, short_term_memory: MemoryManager | MemoryConfig | dict[str, Any] | None=None, long_term_memory: LongTermMemoryConfig | dict[str, Any] | None=None, defaults: dict[str, Any] | None=None, broth_options: dict[str, Any] | None=None, toolkit_catalog_config: ToolkitCatalogConfig | dict[str, Any] | None=None)`

### 公共方法

#### `__init__(self, *, name: str, instructions: str='', provider: str='openai', model: str='gpt-5', api_key: str | None=None, tools: list[Tool | Toolkit | Callable[..., Any]] | None=None, short_term_memory: MemoryManager | MemoryConfig | dict[str, Any] | None=None, long_term_memory: LongTermMemoryConfig | dict[str, Any] | None=None, defaults: dict[str, Any] | None=None, broth_options: dict[str, Any] | None=None, toolkit_catalog_config: ToolkitCatalogConfig | dict[str, Any] | None=None)`

初始化实例，并在类有约束时校验或强制转换构造参数。

- 类型：构造函数
- 定义位置：`src/unchain/agents/agent.py:115`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `enable_subagents(self, *, tool_name: str='spawn_subagent', description: str | None=None, max_depth: int=6, max_children_per_agent: int=10, max_total_subagents: int=100)`

`Agent` 对外暴露的方法 `enable_subagents`。

- 类型：方法
- 定义位置：`src/unchain/agents/agent.py:185`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `enable_toolkit_catalog(self, *, managed_toolkit_ids: tuple[str, ...] | list[str] | None, always_active_toolkit_ids: tuple[str, ...] | list[str] | None=None, registry: dict[str, Any] | None=None, readme_max_chars: int=8000)`

`Agent` 对外暴露的方法 `enable_toolkit_catalog`。

- 类型：方法
- 定义位置：`src/unchain/agents/agent.py:213`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `run(self, messages: str | list[dict[str, Any]] | None, *, payload: dict[str, Any] | None=None, response_format: ResponseFormat | None=None, callback: Callable[[dict[str, Any]], None] | None=None, verbose: bool=False, max_iterations: int | None=None, previous_response_id: str | None=None, on_tool_confirm: Callable | None=None, on_continuation_request: Callable | None=None, session_id: str | None=None, memory_namespace: str | None=None)`

`Agent` 对外暴露的方法 `run`。

- 类型：方法
- 定义位置：`src/unchain/agents/agent.py:613`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `resume_human_input(self, *, conversation: list[dict[str, Any]], continuation: dict[str, Any], response: dict[str, Any] | Any, payload: dict[str, Any] | None=None, response_format: ResponseFormat | None=None, callback: Callable[[dict[str, Any]], None] | None=None, verbose: bool=False, on_tool_confirm: Callable | None=None, on_continuation_request: Callable | None=None, session_id: str | None=None, memory_namespace: str | None=None)`

`Agent` 对外暴露的方法 `resume_human_input`。

- 类型：方法
- 定义位置：`src/unchain/agents/agent.py:695`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `step(self, *, inbox: list[dict[str, Any]], channels: dict[str, list[str]], owner: str, team_transcript: list[dict[str, Any]] | None=None, mode: str='channel_collab', payload: dict[str, Any] | None=None, callback: Callable[[dict[str, Any]], None] | None=None, verbose: bool=False, max_iterations: int | None=None, session_id: str | None=None, memory_namespace: str | None=None)`

`Agent` 对外暴露的方法 `step`。

- 类型：方法
- 定义位置：`src/unchain/agents/agent.py:776`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `as_tool(self, *, name: str | None=None, description: str | None=None)`

`Agent` 对外暴露的方法 `as_tool`。

- 类型：方法
- 定义位置：`src/unchain/agents/agent.py:928`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

### 生命周期与运行时角色

- 构造阶段会校验 identity、复制配置字典，并在需要时把 memory 配置强制转换为 `MemoryManager`。
- `run()` 会先合并 toolkit、创建新的 `Broth`、转发运行时参数，并在暂停时捕获 toolkit catalog continuation 状态。
- `resume_human_input()` 会把挂起的 catalog 状态恢复到新的 runtime，再继续原会话。
- `step()` 是 Team 场景下的包装层，用结构化 step schema 驱动模型输出 publish/handoff/finalize。

### 协作关系与关联类型

- `_SubagentConfig`
- `_SubagentCounters`
- `_SubagentRuntime`

### 最小调用示例

```python
obj = Agent(...)
obj.enable_subagents(...)
```

### `src/unchain/agents/team.py`

多 Agent channel 协作层，负责投递、调度打分、handoff 和 owner 完成控制。

## Team

通过具名 channel 路由 envelope、给待办工作打分，并允许 owner agent 结束多 Agent 运行的协调器。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/unchain/agents/team.py:11` |
| 模块职责 | 多 Agent channel 协作层，负责投递、调度打分、handoff 和 owner 完成控制。 |
| 继承/协议 | `-` |
| 导出状态 | 通过 `unchain` 顶层导出。 |
| 对象类型 | 类；公开或包内可见。 |

### 构造表面

该类主要通过构造函数定义必需输入和校验逻辑。

- `__init__(self, *, agents: list[Agent], owner: str, channels: dict[str, list[str]], mode: str='channel_collab', visible_transcript: bool=True, completion_policy: str='owner_finalize', max_steps: int=24)`

### 公共方法

#### `__init__(self, *, agents: list[Agent], owner: str, channels: dict[str, list[str]], mode: str='channel_collab', visible_transcript: bool=True, completion_policy: str='owner_finalize', max_steps: int=24)`

初始化实例，并在类有约束时校验或强制转换构造参数。

- 类型：构造函数
- 定义位置：`src/unchain/agents/team.py:12`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `run(self, messages: str | list[dict[str, Any]], *, entry_channel: str | None=None, payload: dict[str, Any] | None=None, callback: Callable[[dict[str, Any]], None] | None=None, session_id: str | None=None, memory_namespace: str | None=None, max_steps: int | None=None)`

`Team` 对外暴露的方法 `run`。

- 类型：方法
- 定义位置：`src/unchain/agents/team.py:139`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

### 生命周期与运行时角色

- 构造阶段会规范化 agent 名称、校验 channel 订阅者，并固定 step limit 默认值。
- `run()` 会把初始用户请求转换为 channel envelope，维护每个 agent 的 pending inbox，并持续调度得分最高的 agent。
- 每个 agent 都拿到独立的 session/memory namespace，可向 channel 发布消息、执行 handoff，且只有 owner 可以 finalize。

### 协作关系与关联类型

- `_SubagentConfig`
- `_SubagentCounters`
- `_SubagentRuntime`
- `Agent`

### 最小调用示例

```python
obj = Team(...)
obj.run(...)
```
