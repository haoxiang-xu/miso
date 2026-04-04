# Memory 系统

`memory-system` 主题的正式简体中文 skills 章节。

## 角色与边界

本章说明短期上下文选择、长期资料提取、向量检索和 workspace pin 状态如何通过 `MemoryManager` 协同。

## 依赖关系

- 策略实现 `ContextStrategy` 协议。
- `MemoryManager` 协调 store、strategy、摘要生成、向量检索和长期提取。
- 可选的 Qdrant adapter 为会话级与长期检索提供具体向量后端。

## 核心对象

- `MemoryManager`
- `MemoryConfig`
- `LongTermMemoryConfig`
- `LastNTurnsStrategy`
- `SummaryTokenStrategy`
- `HybridContextStrategy`
- `QdrantVectorAdapter`
- `QdrantLongTermVectorAdapter`

## 执行流与状态流

- 依据会话状态和策略规则准备输入消息。
- 注入 summary、相似检索结果和 pinned context。
- 在一轮完成后提交新的会话状态。
- 若已配置，则持久化长期事实和向量嵌入。

## 配置面

- session store 与 vector adapter。
- summary 阈值与 token 限制。
- 长期 namespace、提取模型和持久化目录。

## 扩展点

- 实现自定义 `SessionStore`、`VectorStoreAdapter` 或 `ContextStrategy`。
- 在内存、JSON、Qdrant 后端之间切换。
- 分别调整检索、摘要和长期提取策略。

## 常见陷阱

- namespace 直接影响长期隔离边界。
- 长期组件在真正需要前是可选的。
- Hybrid 只有在配置 vector adapter 后才会提供检索补充。

## 关联 class 参考

- [Memory API](../api/memory.md)
- [Runtime API](../api/runtime.md)

## 源码入口

- `src/unchain/memory/manager.py`
- `src/unchain/memory/qdrant.py`

## 详细的遗留参考

以下保留了原始仓库 skill 笔记，用于延续性与额外示例。规范副本现已迁入此文档树。

> Memory 层级、配置、上下文策略、namespace 作用域，以及如何用自定义 adapter 扩展。

## Memory 层级

```text
┌─────────────────────────────────────────────────────────┐
│ 第 1 层: Session Store (短期)                           │
│   内存或 JSON 文件键值存储                               │
│   按 session 存储原始对话轮次                            │
├─────────────────────────────────────────────────────────┤
│ 第 2 层: Context Strategy (短期)                        │
│   选择哪些轮次包含在上下文窗口中                         │
│   LastNTurns / SummaryToken / Hybrid                    │
├─────────────────────────────────────────────────────────┤
│ 第 3 层: Vector Store (短期，可选)                      │
│   对近期消息做相似度搜索                                 │
│   通过 embedding 检索相关的旧轮次                        │
├─────────────────────────────────────────────────────────┤
│ 第 4 层: Long-Term Profile (可选)                       │
│   提取的事实、事件、playbook                             │
│   按 namespace 跨 session 持久化                        │
├─────────────────────────────────────────────────────────┤
│ 第 5 层: Long-Term Vectors (可选)                       │
│   Qdrant 支持的 profile 条目语义搜索                     │
│   跨 session 知识检索                                    │
└─────────────────────────────────────────────────────────┘
```

每个层级都是独立可选的。你可以只用第 1-2 层 (基础对话)，也可以全部叠加实现完整持久化。

## 配置

### `MemoryConfig` -- 短期 memory

```python
from unchain.memory import MemoryConfig

config = MemoryConfig(
    last_n_turns=8,                     # 始终包含最近 N 轮
    summary_trigger_pct=0.75,           # 上下文达到窗口 75% 时触发摘要
    summary_target_pct=0.45,            # 摘要后压缩到 45%
    max_summary_chars=2400,             # 摘要本身的最大字符数
    vector_top_k=4,                     # 检索最相似的 4 条历史消息
    vector_adapter=None,                # 可选的 VectorStoreAdapter 实例
    long_term=None,                     # 可选的 LongTermMemoryConfig
    deferred_tool_compaction_enabled=True,  # 压缩旧工具载荷
)
```

### `LongTermMemoryConfig` -- 持久知识

```python
from unchain.memory import LongTermMemoryConfig

lt_config = LongTermMemoryConfig(
    profile_store=my_profile_store,     # LongTermProfileStore 实现
    vector_adapter=my_vector_adapter,   # LongTermVectorAdapter 实现 (如 Qdrant)
    extraction_model=None,              # 用于事实提取的模型 (默认使用 agent 的模型)
    extraction_provider=None,           # 提取用的 provider
)
```

### 传递给 Agent

```python
from unchain import Agent

agent = Agent(
    name="coder",
    provider="openai",
    model="gpt-5",
    short_term_memory=MemoryConfig(last_n_turns=10),
    long_term_memory=LongTermMemoryConfig(
        profile_store=JsonFileLongTermProfileStore(path="./memory"),
    ),
)
```

也可以用 dict (自动转换):

```python
agent = Agent(
    short_term_memory={"last_n_turns": 10},
    long_term_memory={"profile_store": my_store},
)
```

## 上下文策略

策略决定 session store 中的哪些消息被包含在 LLM 的上下文窗口中。

### `LastNTurnsStrategy`

始终包含最后 N 对消息。简单且可预测。

```python
# 通过 MemoryConfig.last_n_turns 配置
config = MemoryConfig(last_n_turns=8)
```

### `SummaryTokenStrategy`

当对话超过模型上下文窗口的一定比例时，旧消息会被摘要成紧凑形式。摘要替换掉详细消息。

```python
config = MemoryConfig(
    summary_trigger_pct=0.75,   # 达到上下文 75% 时开始摘要
    summary_target_pct=0.45,    # 压缩到 45%
    max_summary_chars=2400,
)
```

摘要通过调用 LLM 本身 (经由 Broth) 配合摘要 prompt 生成。

### `HybridContextStrategy`

组合 LastNTurns + SummaryToken。近期轮次始终保留；空间不足时对旧轮次做摘要。提供 `MemoryConfig` 时这是 **默认** 策略。

## Namespace 作用域

memory 通过两个标识符来划定作用域：

| 标识符             | 用途                    | 默认值               |
| ------------------ | ----------------------- | -------------------- |
| `session_id`       | 隔离对话轮次            | 自动生成 UUID        |
| `memory_namespace` | 隔离长期 profile        | 与 `session_id` 相同 |

### 命名约定

| 场景                                   | `session_id`  | `memory_namespace`                   |
| -------------------------------------- | ------------- | ------------------------------------ |
| 单 agent，单次运行                     | UUID          | UUID                                 |
| 单 agent，多次运行 (同一 session)      | 固定用户 ID   | 固定用户 ID                          |
| Team agent                             | UUID          | `{session_id}:{agent_name}`          |
| 子代理                                 | 父级 ID       | `{parent_namespace}:{subagent_name}` |

**关键规则**: 跨 session 使用相同的 `memory_namespace` 来累积长期知识。使用不同的 `session_id` 来隔离对话轮次。

## 工具历史压缩

`tool_history` 模块缩减对话历史中旧工具调用的载荷。

### 功能

每次运行后，**之前轮次** (非当前轮次) 的大型工具参数和结果会被替换为紧凑摘要：

```python
# 压缩前 (对话历史中):
{"tool_call": "read_files", "arguments": {"paths": ["main.py"]}, "result": {"files": [{"content": "... 50,000 chars ..."}]}}

# 压缩后:
{"tool_call": "read_files", "arguments": {"paths": ["main.py"]}, "result": "[compacted: 50000 chars]"}
```

### 配置

```python
config = MemoryConfig(
    deferred_tool_compaction_enabled=True,  # 默认: True
)
```

### 通过 history optimizer 自定义压缩

当默认压缩不够好时，注册每个工具的 optimizer：

```python
self.register(
    self.search_text,
    history_result_optimizer=lambda result: {
        **result,
        "matches": f"[{len(result.get('matches', []))} matches, details omitted]",
    },
)
```

## Session Store

### `InMemorySessionStore` (默认)

临时性 -- 进程退出时对话丢失。

```python
from unchain.memory import InMemorySessionStore

store = InMemorySessionStore()
```

### 自定义 `SessionStore`

实现接口以支持持久化：

```python
from unchain.memory import SessionStore

class MySessionStore(SessionStore):
    def load(self, session_id: str) -> list[dict]:
        """加载 session 的对话轮次。"""
        ...

    def save(self, session_id: str, messages: list[dict]) -> None:
        """保存 session 的对话轮次。"""
        ...

    def delete(self, session_id: str) -> None:
        """删除 session 的所有轮次。"""
        ...
```

## Vector Store Adapter

### `VectorStoreAdapter` (短期相似度搜索)

```python
from unchain.memory import VectorStoreAdapter

class MyVectorAdapter(VectorStoreAdapter):
    def add(self, texts: list[str], metadatas: list[dict], namespace: str) -> None:
        """索引文本片段及元数据。"""
        ...

    def search(self, query: str, top_k: int, namespace: str) -> list[dict]:
        """返回最相似的 top-k 片段。"""
        ...
```

### `LongTermVectorAdapter` (跨 session 知识)

接口形状相同，但操作长期 profile 条目。Qdrant adapter (`unchain.memory.qdrant`) 是参考实现。

## Long-Term Profile Store

```python
from unchain.memory import LongTermProfileStore

class MyProfileStore(LongTermProfileStore):
    def load(self, namespace: str) -> dict:
        """加载 profile (事实、事件、playbook)。"""
        ...

    def save(self, namespace: str, profile: dict) -> None:
        """保存 profile。"""
        ...
```

内置的 `JsonFileLongTermProfileStore` 将 profile 以 JSON 文件形式保存在目录中。

## 运行期间的 Memory 流程

```text
Agent.run(messages, session_id, memory_namespace)
  │
  ▼
MemoryManager.prepare_messages(session_id)
  │  1. 从 SessionStore 加载原始轮次
  │  2. 应用上下文策略 (LastN + Summary)
  │  3. 注入 workspace pin context
  │  4. 注入长期 profile 摘要 (若可用)
  │  5. 检索相似的历史消息 (向量搜索)
  │  6. 返回上下文窗口大小的消息列表
  │
  ▼
Broth.run() -- 使用准备好的消息执行 LLM 循环
  │
  ▼
MemoryManager.commit_messages(session_id, full_conversation)
  │  1. 将所有轮次保存到 SessionStore
  │  2. 应用工具历史压缩
  │  3. 提取长期事实/事件 (异步，经由 LLM)
  │  4. 持久化到 LongTermProfileStore
  │  5. 在 LongTermVectorAdapter 中建索引
  │
  ▼
完成
```

## 常见陷阱

1. **摘要生成会调用 LLM** -- `SummaryTokenStrategy` 会额外发起一次 API 调用来生成摘要。这会增加延迟和 token 成本。如果对话较短，单独使用 `LastNTurnsStrategy` 就够了。

2. **`memory_namespace` vs `session_id`** -- 混淆两者会导致跨 session 数据泄漏 (namespace 错误) 或知识无法累积 (session_id 错误)。参见上方命名表。

3. **vector adapter 是可选的** -- 如果不提供，相似度搜索会被静默跳过。系统不依赖它也能正常工作。

4. **长期提取需要模型** -- 事实提取会调用 LLM。如果未设置 `extraction_model`，则使用 agent 自己的模型，每次运行都会增加 token 成本。

5. **工具压缩是有损的** -- 旧工具结果被替换为摘要。如果 LLM 需要引用之前的精确结果，可能找不到。当前轮次永远不会被压缩。

6. **InMemorySessionStore 是临时的** -- 默认 store 在进程重启时丢失一切。多 session 场景需要实现持久化 `SessionStore`。

## 相关 Skills

- [architecture-overview.md](architecture-overview.md) -- memory 在系统中的位置
- [runtime-engine.md](runtime-engine.md) -- Broth 如何与 MemoryManager 集成
- [agent-and-team.md](agent-and-team.md) -- Team/子代理的 memory namespace 约定
