# Memory API 参考

覆盖短期/长期记忆协议、存储、策略、向量适配器以及 MemoryManager 协调器。

| 指标 | 值 |
| --- | --- |
| 类数量 | 16 |
| Dataclass | 2 |
| 协议 | 5 |
| 仅内部类型 | 0 |

## 覆盖地图

| 类 | 源码 | 导出 | 类型 |
| --- | --- | --- | --- |
| `SessionStore` | `src/miso/memory/manager.py:21` | subpackage | protocol |
| `VectorStoreAdapter` | `src/miso/memory/manager.py:30` | subpackage | protocol |
| `LongTermProfileStore` | `src/miso/memory/manager.py:52` | subpackage | protocol |
| `LongTermVectorAdapter` | `src/miso/memory/manager.py:61` | subpackage | protocol |
| `ContextStrategy` | `src/miso/memory/manager.py:84` | subpackage | protocol |
| `InMemorySessionStore` | `src/miso/memory/manager.py:104` | subpackage | class |
| `JsonFileLongTermProfileStore` | `src/miso/memory/manager.py:117` | subpackage | class |
| `LongTermMemoryConfig` | `src/miso/memory/manager.py:144` | subpackage | dataclass |
| `MemoryConfig` | `src/miso/memory/manager.py:167` | subpackage | dataclass |
| `LastNTurnsStrategy` | `src/miso/memory/manager.py:1642` | subpackage | class |
| `SummaryTokenStrategy` | `src/miso/memory/manager.py:1675` | subpackage | class |
| `HybridContextStrategy` | `src/miso/memory/manager.py:1779` | subpackage | class |
| `MemoryManager` | `src/miso/memory/manager.py:1866` | subpackage | class |
| `QdrantVectorAdapter` | `src/miso/memory/qdrant.py:198` | internal | class |
| `QdrantLongTermVectorAdapter` | `src/miso/memory/qdrant.py:305` | internal | class |
| `JsonFileSessionStore` | `src/miso/memory/qdrant.py:410` | internal | class |

### `src/miso/memory/manager.py`

定义 memory 协议、配置、策略，并提供顶层 MemoryManager 协调层。

## SessionStore

协议类型，用来定义定义 memory 协议、配置、策略，并提供顶层 MemoryManager 协调层中的稳定契约。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/miso/memory/manager.py:21` |
| 模块职责 | 定义 memory 协议、配置、策略，并提供顶层 MemoryManager 协调层。 |
| 继承/协议 | `Protocol` |
| 导出状态 | 通过所属子包 `__init__` 导出。 |
| 对象类型 | 协议；公开或包内可见。 |

### 公共方法

#### `load(self, session_id: str)`

`SessionStore` 对外暴露的方法 `load`。

- 类型：方法
- 定义位置：`src/miso/memory/manager.py:22`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `save(self, session_id: str, state: dict[str, Any])`

`SessionStore` 对外暴露的方法 `save`。

- 类型：方法
- 定义位置：`src/miso/memory/manager.py:25`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

### 协作关系与关联类型

- `VectorStoreAdapter`
- `LongTermProfileStore`
- `LongTermVectorAdapter`
- `ContextStrategy`
- `InMemorySessionStore`

### 最小调用示例

```python
class Demo(...):
    pass
```

## VectorStoreAdapter

协议类型，用来定义定义 memory 协议、配置、策略，并提供顶层 MemoryManager 协调层中的稳定契约。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/miso/memory/manager.py:30` |
| 模块职责 | 定义 memory 协议、配置、策略，并提供顶层 MemoryManager 协调层。 |
| 继承/协议 | `Protocol` |
| 导出状态 | 通过所属子包 `__init__` 导出。 |
| 对象类型 | 协议；公开或包内可见。 |

### 公共方法

#### `add_texts(self, *, session_id: str, texts: list[str], metadatas: list[dict[str, Any]])`

`VectorStoreAdapter` 对外暴露的方法 `add_texts`。

- 类型：方法
- 定义位置：`src/miso/memory/manager.py:31`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `similarity_search(self, *, session_id: str, query: str, k: int, min_score: float | None=None)`

`VectorStoreAdapter` 对外暴露的方法 `similarity_search`。

- 类型：方法
- 定义位置：`src/miso/memory/manager.py:40`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

### 协作关系与关联类型

- `SessionStore`
- `LongTermProfileStore`
- `LongTermVectorAdapter`
- `ContextStrategy`
- `InMemorySessionStore`

### 最小调用示例

```python
class Demo(...):
    pass
```

## LongTermProfileStore

协议类型，用来定义定义 memory 协议、配置、策略，并提供顶层 MemoryManager 协调层中的稳定契约。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/miso/memory/manager.py:52` |
| 模块职责 | 定义 memory 协议、配置、策略，并提供顶层 MemoryManager 协调层。 |
| 继承/协议 | `Protocol` |
| 导出状态 | 通过所属子包 `__init__` 导出。 |
| 对象类型 | 协议；公开或包内可见。 |

### 公共方法

#### `load(self, namespace: str)`

`LongTermProfileStore` 对外暴露的方法 `load`。

- 类型：方法
- 定义位置：`src/miso/memory/manager.py:53`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `save(self, namespace: str, profile: dict[str, Any])`

`LongTermProfileStore` 对外暴露的方法 `save`。

- 类型：方法
- 定义位置：`src/miso/memory/manager.py:56`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

### 协作关系与关联类型

- `SessionStore`
- `VectorStoreAdapter`
- `LongTermVectorAdapter`
- `ContextStrategy`
- `InMemorySessionStore`

### 最小调用示例

```python
class Demo(...):
    pass
```

## LongTermVectorAdapter

协议类型，用来定义定义 memory 协议、配置、策略，并提供顶层 MemoryManager 协调层中的稳定契约。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/miso/memory/manager.py:61` |
| 模块职责 | 定义 memory 协议、配置、策略，并提供顶层 MemoryManager 协调层。 |
| 继承/协议 | `Protocol` |
| 导出状态 | 通过所属子包 `__init__` 导出。 |
| 对象类型 | 协议；公开或包内可见。 |

### 公共方法

#### `add_texts(self, *, namespace: str, texts: list[str], metadatas: list[dict[str, Any]])`

`LongTermVectorAdapter` 对外暴露的方法 `add_texts`。

- 类型：方法
- 定义位置：`src/miso/memory/manager.py:62`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `similarity_search(self, *, namespace: str, query: str, k: int, filters: dict[str, Any] | None=None, min_score: float | None=None)`

`LongTermVectorAdapter` 对外暴露的方法 `similarity_search`。

- 类型：方法
- 定义位置：`src/miso/memory/manager.py:71`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

### 协作关系与关联类型

- `SessionStore`
- `VectorStoreAdapter`
- `LongTermProfileStore`
- `ContextStrategy`
- `InMemorySessionStore`

### 最小调用示例

```python
class Demo(...):
    pass
```

## ContextStrategy

协议类型，用来定义定义 memory 协议、配置、策略，并提供顶层 MemoryManager 协调层中的稳定契约。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/miso/memory/manager.py:84` |
| 模块职责 | 定义 memory 协议、配置、策略，并提供顶层 MemoryManager 协调层。 |
| 继承/协议 | `Protocol` |
| 导出状态 | 通过所属子包 `__init__` 导出。 |
| 对象类型 | 协议；公开或包内可见。 |

### 公共方法

#### `prepare(self, *, state: dict[str, Any], incoming: list[dict[str, Any]], max_context_window_tokens: int, model: str)`

`ContextStrategy` 对外暴露的方法 `prepare`。

- 类型：方法
- 定义位置：`src/miso/memory/manager.py:85`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `commit(self, *, state: dict[str, Any], full_conversation: list[dict[str, Any]])`

`ContextStrategy` 对外暴露的方法 `commit`。

- 类型：方法
- 定义位置：`src/miso/memory/manager.py:95`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

### 协作关系与关联类型

- `SessionStore`
- `VectorStoreAdapter`
- `LongTermProfileStore`
- `LongTermVectorAdapter`
- `InMemorySessionStore`

### 最小调用示例

```python
class Demo(...):
    pass
```

## InMemorySessionStore

用于定义 memory 协议、配置、策略，并提供顶层 MemoryManager 协调层的实现类。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/miso/memory/manager.py:104` |
| 模块职责 | 定义 memory 协议、配置、策略，并提供顶层 MemoryManager 协调层。 |
| 继承/协议 | `-` |
| 导出状态 | 通过所属子包 `__init__` 导出。 |
| 对象类型 | 类；公开或包内可见。 |

### 构造表面

该类主要通过构造函数定义必需输入和校验逻辑。

- `__init__(self)`

### 公共方法

#### `__init__(self)`

初始化实例，并在类有约束时校验或强制转换构造参数。

- 类型：构造函数
- 定义位置：`src/miso/memory/manager.py:107`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `load(self, session_id: str)`

`InMemorySessionStore` 对外暴露的方法 `load`。

- 类型：方法
- 定义位置：`src/miso/memory/manager.py:110`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `save(self, session_id: str, state: dict[str, Any])`

`InMemorySessionStore` 对外暴露的方法 `save`。

- 类型：方法
- 定义位置：`src/miso/memory/manager.py:113`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

### 协作关系与关联类型

- `SessionStore`
- `VectorStoreAdapter`
- `LongTermProfileStore`
- `LongTermVectorAdapter`
- `ContextStrategy`

### 最小调用示例

```python
obj = InMemorySessionStore(...)
obj.load(...)
```

## JsonFileLongTermProfileStore

用于定义 memory 协议、配置、策略，并提供顶层 MemoryManager 协调层的实现类。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/miso/memory/manager.py:117` |
| 模块职责 | 定义 memory 协议、配置、策略，并提供顶层 MemoryManager 协调层。 |
| 继承/协议 | `-` |
| 导出状态 | 通过所属子包 `__init__` 导出。 |
| 对象类型 | 类；公开或包内可见。 |

### 构造表面

该类主要通过构造函数定义必需输入和校验逻辑。

- `__init__(self, base_dir: str | Path | None=None)`

### 公共方法

#### `__init__(self, base_dir: str | Path | None=None)`

初始化实例，并在类有约束时校验或强制转换构造参数。

- 类型：构造函数
- 定义位置：`src/miso/memory/manager.py:120`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `load(self, namespace: str)`

`JsonFileLongTermProfileStore` 对外暴露的方法 `load`。

- 类型：方法
- 定义位置：`src/miso/memory/manager.py:128`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `save(self, namespace: str, profile: dict[str, Any])`

`JsonFileLongTermProfileStore` 对外暴露的方法 `save`。

- 类型：方法
- 定义位置：`src/miso/memory/manager.py:138`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

### 协作关系与关联类型

- `SessionStore`
- `VectorStoreAdapter`
- `LongTermProfileStore`
- `LongTermVectorAdapter`
- `ContextStrategy`

### 最小调用示例

```python
obj = JsonFileLongTermProfileStore(...)
obj.load(...)
```

## LongTermMemoryConfig

用于定义 memory 协议、配置、策略，并提供顶层 MemoryManager 协调层的 dataclass 载荷。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/miso/memory/manager.py:144` |
| 模块职责 | 定义 memory 协议、配置、策略，并提供顶层 MemoryManager 协调层。 |
| 继承/协议 | `-` |
| 导出状态 | 通过所属子包 `__init__` 导出。 |
| 对象类型 | Dataclass；公开或包内可见。 |

### 字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `profile_store` | `LongTermProfileStore | None` | 默认值：`None`。 |
| `vector_adapter` | `LongTermVectorAdapter | None` | 默认值：`None`。 |
| `extractor` | `LongTermExtractor | None` | 默认值：`None`。 |
| `vector_top_k` | `int` | 默认值：`4`。 |
| `vector_min_score` | `float | None` | 默认值：`None`。 |
| `episode_top_k` | `int` | 默认值：`2`。 |
| `episode_min_score` | `float | None` | 默认值：`None`。 |
| `playbook_top_k` | `int` | 默认值：`2`。 |
| `playbook_min_score` | `float | None` | 默认值：`None`。 |
| `max_profile_chars` | `int` | 默认值：`1200`。 |
| `max_fact_items` | `int` | 默认值：`6`。 |
| `max_episode_items` | `int` | 默认值：`3`。 |
| `max_playbook_items` | `int` | 默认值：`2`。 |
| `extract_every_n_turns` | `int` | 默认值：`1`。 |
| `embedding_model` | `str` | 默认值：`'text-embedding-3-small'`。 |
| `embedding_payload` | `dict[str, Any] | None` | 默认值：`None`。 |
| `profile_base_dir` | `str | Path | None` | 默认值：`None`。 |
| `qdrant_path` | `str | Path | None` | 默认值：`None`。 |
| `collection_prefix` | `str` | 默认值：`'long_term'`。 |

### 公共方法

该类型除了 dataclass/protocol 结构外不暴露公共方法。

### 协作关系与关联类型

- `SessionStore`
- `VectorStoreAdapter`
- `LongTermProfileStore`
- `LongTermVectorAdapter`
- `ContextStrategy`

### 最小调用示例

```python
LongTermMemoryConfig(profile_store=..., vector_adapter=..., extractor=..., vector_top_k=...)
```

## MemoryConfig

用于定义 memory 协议、配置、策略，并提供顶层 MemoryManager 协调层的 dataclass 载荷。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/miso/memory/manager.py:167` |
| 模块职责 | 定义 memory 协议、配置、策略，并提供顶层 MemoryManager 协调层。 |
| 继承/协议 | `-` |
| 导出状态 | 通过所属子包 `__init__` 导出。 |
| 对象类型 | Dataclass；公开或包内可见。 |

### 字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `last_n_turns` | `int` | 默认值：`8`。 |
| `summary_trigger_pct` | `float` | 默认值：`0.75`。 |
| `summary_target_pct` | `float` | 默认值：`0.45`。 |
| `max_summary_chars` | `int` | 默认值：`2400`。 |
| `vector_top_k` | `int` | 默认值：`4`。 |
| `vector_min_score` | `float | None` | 默认值：`None`。 |
| `vector_adapter` | `VectorStoreAdapter | None` | 默认值：`None`。 |
| `long_term` | `LongTermMemoryConfig | None` | 默认值：`None`。 |
| `deferred_tool_compaction_enabled` | `bool` | 默认值：`True`。 |
| `deferred_tool_compaction_keep_completed_turns` | `int` | 默认值：`1`。 |
| `deferred_tool_compaction_max_chars` | `int` | 默认值：`1200`。 |
| `deferred_tool_compaction_preview_chars` | `int` | 默认值：`160`。 |
| `deferred_tool_compaction_include_tools` | `list[str] | None` | 默认值：`None`。 |
| `deferred_tool_compaction_hash_payloads` | `bool` | 默认值：`True`。 |

### 公共方法

该类型除了 dataclass/protocol 结构外不暴露公共方法。

### 协作关系与关联类型

- `SessionStore`
- `VectorStoreAdapter`
- `LongTermProfileStore`
- `LongTermVectorAdapter`
- `ContextStrategy`

### 最小调用示例

```python
MemoryConfig(last_n_turns=..., summary_trigger_pct=..., summary_target_pct=..., max_summary_chars=...)
```

## LastNTurnsStrategy

用于定义 memory 协议、配置、策略，并提供顶层 MemoryManager 协调层的实现类。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/miso/memory/manager.py:1642` |
| 模块职责 | 定义 memory 协议、配置、策略，并提供顶层 MemoryManager 协调层。 |
| 继承/协议 | `-` |
| 导出状态 | 通过所属子包 `__init__` 导出。 |
| 对象类型 | 类；公开或包内可见。 |

### 构造表面

该类主要通过构造函数定义必需输入和校验逻辑。

- `__init__(self, last_n_turns: int=8)`

### 公共方法

#### `__init__(self, last_n_turns: int=8)`

初始化实例，并在类有约束时校验或强制转换构造参数。

- 类型：构造函数
- 定义位置：`src/miso/memory/manager.py:1643`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `prepare(self, *, state: dict[str, Any], incoming: list[dict[str, Any]], max_context_window_tokens: int, model: str)`

`LastNTurnsStrategy` 对外暴露的方法 `prepare`。

- 类型：方法
- 定义位置：`src/miso/memory/manager.py:1646`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `commit(self, *, state: dict[str, Any], full_conversation: list[dict[str, Any]])`

`LastNTurnsStrategy` 对外暴露的方法 `commit`。

- 类型：方法
- 定义位置：`src/miso/memory/manager.py:1666`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

### 协作关系与关联类型

- `SessionStore`
- `VectorStoreAdapter`
- `LongTermProfileStore`
- `LongTermVectorAdapter`
- `ContextStrategy`

### 最小调用示例

```python
obj = LastNTurnsStrategy(...)
obj.prepare(...)
```

## SummaryTokenStrategy

用于定义 memory 协议、配置、策略，并提供顶层 MemoryManager 协调层的实现类。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/miso/memory/manager.py:1675` |
| 模块职责 | 定义 memory 协议、配置、策略，并提供顶层 MemoryManager 协调层。 |
| 继承/协议 | `-` |
| 导出状态 | 通过所属子包 `__init__` 导出。 |
| 对象类型 | 类；公开或包内可见。 |

### 构造表面

该类主要通过构造函数定义必需输入和校验逻辑。

- `__init__(self, *, summary_trigger_pct: float=0.75, summary_target_pct: float=0.45, max_summary_chars: int=2400)`

### 公共方法

#### `__init__(self, *, summary_trigger_pct: float=0.75, summary_target_pct: float=0.45, max_summary_chars: int=2400)`

初始化实例，并在类有约束时校验或强制转换构造参数。

- 类型：构造函数
- 定义位置：`src/miso/memory/manager.py:1676`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `prepare(self, *, state: dict[str, Any], incoming: list[dict[str, Any]], max_context_window_tokens: int, model: str)`

`SummaryTokenStrategy` 对外暴露的方法 `prepare`。

- 类型：方法
- 定义位置：`src/miso/memory/manager.py:1687`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `commit(self, *, state: dict[str, Any], full_conversation: list[dict[str, Any]])`

`SummaryTokenStrategy` 对外暴露的方法 `commit`。

- 类型：方法
- 定义位置：`src/miso/memory/manager.py:1770`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

### 协作关系与关联类型

- `SessionStore`
- `VectorStoreAdapter`
- `LongTermProfileStore`
- `LongTermVectorAdapter`
- `ContextStrategy`

### 最小调用示例

```python
obj = SummaryTokenStrategy(...)
obj.prepare(...)
```

## HybridContextStrategy

用于定义 memory 协议、配置、策略，并提供顶层 MemoryManager 协调层的实现类。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/miso/memory/manager.py:1779` |
| 模块职责 | 定义 memory 协议、配置、策略，并提供顶层 MemoryManager 协调层。 |
| 继承/协议 | `-` |
| 导出状态 | 通过所属子包 `__init__` 导出。 |
| 对象类型 | 类；公开或包内可见。 |

### 构造表面

该类主要通过构造函数定义必需输入和校验逻辑。

- `__init__(self, *, summary_strategy: SummaryTokenStrategy, last_n_strategy: LastNTurnsStrategy, vector_top_k: int=4, vector_min_score: float | None=None, vector_adapter: VectorStoreAdapter | None=None)`

### 公共方法

#### `__init__(self, *, summary_strategy: SummaryTokenStrategy, last_n_strategy: LastNTurnsStrategy, vector_top_k: int=4, vector_min_score: float | None=None, vector_adapter: VectorStoreAdapter | None=None)`

初始化实例，并在类有约束时校验或强制转换构造参数。

- 类型：构造函数
- 定义位置：`src/miso/memory/manager.py:1780`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `prepare(self, *, state: dict[str, Any], incoming: list[dict[str, Any]], max_context_window_tokens: int, model: str)`

`HybridContextStrategy` 对外暴露的方法 `prepare`。

- 类型：方法
- 定义位置：`src/miso/memory/manager.py:1795`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `commit(self, *, state: dict[str, Any], full_conversation: list[dict[str, Any]])`

`HybridContextStrategy` 对外暴露的方法 `commit`。

- 类型：方法
- 定义位置：`src/miso/memory/manager.py:1856`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

### 协作关系与关联类型

- `SessionStore`
- `VectorStoreAdapter`
- `LongTermProfileStore`
- `LongTermVectorAdapter`
- `ContextStrategy`

### 最小调用示例

```python
obj = HybridContextStrategy(...)
obj.prepare(...)
```

## MemoryManager

顶层 memory 协调器，负责依据会话状态准备消息，并把新一轮对话写回短期/长期存储。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/miso/memory/manager.py:1866` |
| 模块职责 | 定义 memory 协议、配置、策略，并提供顶层 MemoryManager 协调层。 |
| 继承/协议 | `-` |
| 导出状态 | 通过所属子包 `__init__` 导出。 |
| 对象类型 | 类；公开或包内可见。 |

### 构造表面

该类主要通过构造函数定义必需输入和校验逻辑。

- `__init__(self, config: MemoryConfig | None=None, store: SessionStore | None=None, strategy: ContextStrategy | None=None)`

### 属性

- `@property last_prepare_info`: 公开属性访问器。
- `@property last_commit_info`: 公开属性访问器。

### 公共方法

#### `__init__(self, config: MemoryConfig | None=None, store: SessionStore | None=None, strategy: ContextStrategy | None=None)`

初始化实例，并在类有约束时校验或强制转换构造参数。

- 类型：构造函数
- 定义位置：`src/miso/memory/manager.py:1867`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `ensure_long_term_components(self, *, broth_instance: Any | None=None)`

`MemoryManager` 对外暴露的方法 `ensure_long_term_components`。

- 类型：方法
- 定义位置：`src/miso/memory/manager.py:1897`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `estimate_tokens(self, messages: list[dict[str, Any]])`

`MemoryManager` 对外暴露的方法 `estimate_tokens`。

- 类型：方法
- 定义位置：`src/miso/memory/manager.py:1919`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `prepare_messages(self, session_id: str, incoming: list[dict[str, Any]], *, max_context_window_tokens: int, model: str, summary_generator: SummaryGenerator | None=None, memory_namespace: str | None=None, provider: str | None=None, tool_resolver: HistoryToolResolver | None=None)`

`MemoryManager` 对外暴露的方法 `prepare_messages`。

- 类型：方法
- 定义位置：`src/miso/memory/manager.py:2070`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `commit_messages(self, session_id: str, full_conversation: list[dict[str, Any]], *, memory_namespace: str | None=None, model: str | None=None, long_term_extractor: LongTermExtractor | None=None)`

`MemoryManager` 对外暴露的方法 `commit_messages`。

- 类型：方法
- 定义位置：`src/miso/memory/manager.py:2269`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

### 生命周期与运行时角色

- 初始化时绑定 config、session store 与选定的 context strategy。
- `prepare_messages()` 会加载会话状态，让 strategy 生成工作上下文，并可追加 summary 或向量检索结果。
- `commit_messages()` 会持久化新的短期状态，并按需写入长期事实/profile 更新。
- 长期组件是懒创建的；只有配置启用时才需要 Qdrant/profile store。

### 协作关系与关联类型

- `SessionStore`
- `VectorStoreAdapter`
- `LongTermProfileStore`
- `LongTermVectorAdapter`
- `ContextStrategy`

### 最小调用示例

```python
obj = MemoryManager(...)
obj.ensure_long_term_components(...)
```

### `src/miso/memory/qdrant.py`

Qdrant 向量适配器，以及 JSON 会话存储实现。

## QdrantVectorAdapter

用于Qdrant 向量适配器，以及 JSON 会话存储实现的实现类。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/miso/memory/qdrant.py:198` |
| 模块职责 | Qdrant 向量适配器，以及 JSON 会话存储实现。 |
| 继承/协议 | `-` |
| 导出状态 | 未导出，应视为实现细节。 |
| 对象类型 | 类；公开或包内可见。 |

### 构造表面

该类主要通过构造函数定义必需输入和校验逻辑。

- `__init__(self, client: 'QdrantClient', embed_fn, vector_size: int, collection_prefix: str='chat')`

### 公共方法

#### `__init__(self, client: 'QdrantClient', embed_fn, vector_size: int, collection_prefix: str='chat')`

初始化实例，并在类有约束时校验或强制转换构造参数。

- 类型：构造函数
- 定义位置：`src/miso/memory/qdrant.py:205`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `add_texts(self, *, session_id: str, texts: list[str], metadatas: list[dict[str, Any]])`

`QdrantVectorAdapter` 对外暴露的方法 `add_texts`。

- 类型：方法
- 定义位置：`src/miso/memory/qdrant.py:236`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `similarity_search(self, *, session_id: str, query: str, k: int, min_score: float | None=None)`

`QdrantVectorAdapter` 对外暴露的方法 `similarity_search`。

- 类型：方法
- 定义位置：`src/miso/memory/qdrant.py:256`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

### 协作关系与关联类型

- `QdrantLongTermVectorAdapter`
- `JsonFileSessionStore`

### 最小调用示例

```python
obj = QdrantVectorAdapter(...)
obj.add_texts(...)
```

## QdrantLongTermVectorAdapter

用于Qdrant 向量适配器，以及 JSON 会话存储实现的实现类。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/miso/memory/qdrant.py:305` |
| 模块职责 | Qdrant 向量适配器，以及 JSON 会话存储实现。 |
| 继承/协议 | `-` |
| 导出状态 | 未导出，应视为实现细节。 |
| 对象类型 | 类；公开或包内可见。 |

### 构造表面

该类主要通过构造函数定义必需输入和校验逻辑。

- `__init__(self, client: 'QdrantClient', embed_fn, vector_size: int, collection_prefix: str='long_term')`

### 公共方法

#### `__init__(self, client: 'QdrantClient', embed_fn, vector_size: int, collection_prefix: str='long_term')`

初始化实例，并在类有约束时校验或强制转换构造参数。

- 类型：构造函数
- 定义位置：`src/miso/memory/qdrant.py:312`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `add_texts(self, *, namespace: str, texts: list[str], metadatas: list[dict[str, Any]])`

`QdrantLongTermVectorAdapter` 对外暴露的方法 `add_texts`。

- 类型：方法
- 定义位置：`src/miso/memory/qdrant.py:343`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `similarity_search(self, *, namespace: str, query: str, k: int, filters: dict[str, Any] | None=None, min_score: float | None=None)`

`QdrantLongTermVectorAdapter` 对外暴露的方法 `similarity_search`。

- 类型：方法
- 定义位置：`src/miso/memory/qdrant.py:363`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

### 协作关系与关联类型

- `QdrantVectorAdapter`
- `JsonFileSessionStore`

### 最小调用示例

```python
obj = QdrantLongTermVectorAdapter(...)
obj.add_texts(...)
```

## JsonFileSessionStore

用于Qdrant 向量适配器，以及 JSON 会话存储实现的实现类。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/miso/memory/qdrant.py:410` |
| 模块职责 | Qdrant 向量适配器，以及 JSON 会话存储实现。 |
| 继承/协议 | `-` |
| 导出状态 | 未导出，应视为实现细节。 |
| 对象类型 | 类；公开或包内可见。 |

### 构造表面

该类主要通过构造函数定义必需输入和校验逻辑。

- `__init__(self, base_dir: str | Path)`

### 公共方法

#### `__init__(self, base_dir: str | Path)`

初始化实例，并在类有约束时校验或强制转换构造参数。

- 类型：构造函数
- 定义位置：`src/miso/memory/qdrant.py:421`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `load(self, session_id: str)`

`JsonFileSessionStore` 对外暴露的方法 `load`。

- 类型：方法
- 定义位置：`src/miso/memory/qdrant.py:429`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `save(self, session_id: str, state: dict[str, Any])`

`JsonFileSessionStore` 对外暴露的方法 `save`。

- 类型：方法
- 定义位置：`src/miso/memory/qdrant.py:438`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

### 协作关系与关联类型

- `QdrantVectorAdapter`
- `QdrantLongTermVectorAdapter`

### 最小调用示例

```python
obj = JsonFileSessionStore(...)
obj.load(...)
```
