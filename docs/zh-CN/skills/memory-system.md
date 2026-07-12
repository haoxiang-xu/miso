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
from unchain.agent import MemoryModule

agent = Agent(
    name="coder",
    provider="openai",
    model="gpt-5",
    modules=(
        MemoryModule(memory=MemoryConfig(
            last_n_turns=10,
            long_term=LongTermMemoryConfig(
                profile_store=JsonFileLongTermProfileStore(path="./memory"),
            ),
        )),
    ),
)
```

`MemoryModule` 接受 `KernelMemoryRuntime`、`MemoryManager`、`MemoryConfig` 或 dict（自动 coerce）。传 config 让记忆行为保持声明式；传 `KernelMemoryRuntime` 可以让多个 agent 复用同一个 runtime。

同一个 `Agent` 实例会跨调用复用由 config 创建的 runtime，因此固定 `session_id` 能保留历史；但默认 store 仍然只在当前进程有效。需要重启恢复时，应显式提供持久 store：

```python
from unchain.memory import JsonFileSessionStore, MemoryConfig, MemoryManager

manager = MemoryManager(
    config=MemoryConfig(last_n_turns=10),
    store=JsonFileSessionStore("./session-state"),
)
agent = Agent(
    name="durable-coder",
    modules=(MemoryModule(memory=manager),),
)
```

## Semantic Memory 与 Execution Checkpoint

Session store 刻意分成两层：

| 层 | 用途 | 能否被摘要或清洗 |
| --- | --- | --- |
| `messages` | 已完成、面向人的 semantic conversation | 可以 |
| `execution_checkpoint` | 未完成工具事务、continuation、provider replay frame、完整性和 tool-schema digest | 不可以 |

- `completed`：在同一次 CAS 写入中原子提交 semantic messages，并按 checkpoint ID 条件清除匹配的 checkpoint。
- `max_iterations`：semantic messages 保持不变，单独保存 execution checkpoint。
- `awaiting_human_input`：semantic messages 保持不变，保存 transcript 与 continuation。

之后使用相同 `session_id` 调 `agent.run(...)`，可从 `max_iterations` checkpoint 继续，已经完成的工具不会再次执行。冷恢复时，`max_iterations=N` 表示给这次新调用再增加 N 个模型迭代；累计 iteration 仍会恢复，用于 telemetry。遇到 awaiting-human checkpoint 时，fresh run 会 fail closed；可用 `agent.resume_human_input(session_id=..., response=...)` 直接从持久 checkpoint 恢复，无需旧进程里的 result 对象。

Replay frame 是 provider-native 且有序的：OpenAI 保留 encrypted reasoning item，Anthropic/Hyperspace 保留 thinking signature，Ollama 保留 thinking 字段。它会校验完整性，并绑定 provider/model 与当前 tool schema；不会进入普通 memory compaction，请求 trace 中也会脱敏。

保证从 checkpoint 写入并回读验证成功后开始。如果进程恰好在“外部工具已经产生副作用、checkpoint 还没落盘”之间崩溃，仍需 idempotency key、write-ahead log 或 transactional outbox；仅靠 execution checkpoint 无法提供任意崩溃下的 exactly-once。

对于 memory-backed `session_id`，全部 `on_suspend` contribution 会先完成，然后才跨过保留的持久化 barrier。阻塞式 `on_human_input`、`on_max_iterations` callback 及其对应 request event，只会在 checkpoint 写入并回读成功后执行；final message event 也只会在 durable finalization 后发出。因此 checkpoint 写入失败时，运行不会进入长期等待。callback 返回的用户回答目前还不是 exactly-once 的持久 interaction record；如果用户已经回答、但进程在 resume delta 应用前崩溃，调用方可能仍需重新提交该回答。

当前 execution checkpoint 恢复的是内建 continuation 边界：semantic transcript、provider replay frame、累计 iteration/token、context-window 大小和 workspace-change state。它还不是任意 harness 状态的通用序列化格式。自定义 `component_state`、optimizer 内部状态、artifact 与 subagent state 如果也要跨进程冷恢复，后续仍需 versioned per-harness checkpoint slice 协议。

每个内置 session store 还会为整份 session state 维护单调 revision。Bootstrap 捕获 revision；semantic commit、checkpoint 保存/清除、workspace pin 修改以及 edit/resend 替换都通过 compare-and-swap（CAS）写入。过期 worker 会抛出 `SessionRevisionConflictError`，不会覆盖较新的 messages 或 checkpoint；重复写入同一个确定性 checkpoint 是幂等的。

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

摘要通过用摘要 prompt 在 agent 自己的模型上重新进入 kernel 来生成。

### `HybridContextStrategy`

组合 LastNTurns + SummaryToken。近期轮次始终保留；空间不足时对旧轮次做摘要。提供 `MemoryConfig` 时这是 **默认** 策略。

压缩按事务处理：只有 summary 阶段成功生成非空替代内容后，LastN 才能删除
源轮次。如果 generator 缺失、报错或返回空摘要，原始上下文会保留，并记录
`upstream_summary_replacement_unavailable`，不会静默丢掉早期证据。

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
| 子代理                                 | 父级 ID       | `{parent_namespace}:{subagent_name}` |
| 嵌套子代理                             | 根 ID         | `{root}:{parent}:{child}`            |

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
    def load(self, session_id: str) -> dict:
        """加载完整 session state。"""
        ...

    def save(self, session_id: str, state: dict) -> None:
        """无条件保存完整 session state。"""
        ...
```

旧接口仍然兼容，但会报告 `session_consistency="best_effort"`，无法阻止跨进程的过期写入。长时任务使用的生产 store 应同时实现可选 revision capability：

```python
from unchain.memory import SessionSnapshot

def load_with_revision(session_id: str) -> SessionSnapshot:
    ...

def save_if_revision(
    session_id: str,
    state: dict,
    expected_revision: int,
) -> int:
    """原子保存；revision 不匹配时抛出 SessionRevisionConflictError。"""
    ...
```

`InMemorySessionStore` 提供进程内 CAS。`JsonFileSessionStore` 通过每个 session 的文件锁、`fsync` 和原子替换提供跨进程 CAS；持久文件损坏时会 fail closed，不会被当成空 session 覆盖。

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

`MemoryModule` 在 kernel 上挂两个 harness：一个在每次模型 turn 前 recall 记忆，一个在每次迭代后 commit 记忆。流程长这样：

```text
Agent.run(messages, session_id=..., memory_namespace=...)
  │
  ▼
KernelLoop.run() — bootstrap + before_model 阶段：
  MemoryManager.prepare_messages(session_id)
  │  1. 从 SessionStore 加载原始轮次
  │  2. 应用上下文策略 (LastN + Summary)
  │  3. 注入 workspace pin context
  │  4. 注入长期 profile 摘要 (若可用)
  │  5. 检索相似的历史消息 (向量搜索)
  │  6. 返回上下文窗口大小的消息列表
  │
  ▼
ModelIO.fetch_turn(...) — 用准备好的消息调 provider
  │
  ▼
KernelLoop.run() — before_commit 阶段：
  MemoryManager.commit_messages(session_id, full_conversation)
  │  1. 将所有轮次保存到 SessionStore
  │  2. 应用工具历史压缩
  │  3. 提取长期事实/事件 (经由 LLM)
  │  4. 持久化到 LongTermProfileStore
  │  5. 在 LongTermVectorAdapter 中建索引
  │
  ▼
KernelRunResult 返回给调用方
```

## 常见陷阱

1. **摘要生成会调用 LLM** -- `SummaryTokenStrategy` 会额外发起一次 API 调用来生成摘要。这会增加延迟和 token 成本。如果对话较短，单独使用 `LastNTurnsStrategy` 就够了。

2. **`memory_namespace` vs `session_id`** -- 混淆两者会导致跨 session 数据泄漏 (namespace 错误) 或知识无法累积 (session_id 错误)。参见上方命名表。

3. **vector adapter 是可选的** -- 如果不提供，相似度搜索会被静默跳过。系统不依赖它也能正常工作。

4. **长期提取需要模型** -- 事实提取会调用 LLM。如果未设置 `extraction_model`，则使用 agent 自己的模型，每次运行都会增加 token 成本。

5. **工具压缩是有损的** -- 旧工具结果被替换为摘要。如果 LLM 需要引用之前的精确结果，可能找不到。当前轮次永远不会被压缩。

6. **InMemorySessionStore 是临时的** -- 默认 store 在进程重启时丢失一切。需要重启恢复时，应使用 `JsonFileSessionStore` 或其他带 revision 的持久 store。仅实现 `load/save` 的旧 store 仍可运行，但并发一致性只是 best-effort。

## 相关 Skills

- [architecture-overview.md](architecture-overview.md) -- memory 在系统中的位置
- [runtime-engine.md](runtime-engine.md) -- memory harness 怎么挂进 `KernelLoop`
- [agent-and-team.md](agent-and-team.md) -- 子代理的 memory namespace 约定
