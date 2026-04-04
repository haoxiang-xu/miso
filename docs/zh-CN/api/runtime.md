# Runtime API 参考

覆盖 provider turn、工具执行、token 统计以及 Broth 主运行时。

| 指标 | 值 |
| --- | --- |
| 类数量 | 5 |
| Dataclass | 4 |
| 协议 | 0 |
| 仅内部类型 | 0 |

## 覆盖地图

| 类 | 源码 | 导出 | 类型 |
| --- | --- | --- | --- |
| `ToolCall` | `src/unchain/runtime/engine.py:68` | subpackage | dataclass |
| `ProviderTurnResult` | `src/unchain/runtime/engine.py:74` | subpackage | dataclass |
| `TokenUsage` | `src/unchain/runtime/engine.py:86` | internal | dataclass |
| `ToolExecutionOutcome` | `src/unchain/runtime/engine.py:93` | subpackage | dataclass |
| `Broth` | `src/unchain/runtime/engine.py:103` | subpackage | class |

### `src/unchain/runtime/engine.py`

面向 provider 的执行循环，以及统一的消息/工具执行数据类型。

## ToolCall

用于面向 provider 的执行循环，以及统一的消息/工具执行数据类型的 dataclass 载荷。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/unchain/runtime/engine.py:68` |
| 模块职责 | 面向 provider 的执行循环，以及统一的消息/工具执行数据类型。 |
| 继承/协议 | `-` |
| 导出状态 | 通过所属子包 `__init__` 导出。 |
| 对象类型 | Dataclass；公开或包内可见。 |

### 字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `call_id` | `str` | 构造时必需。 |
| `name` | `str` | 构造时必需。 |
| `arguments` | `dict[str, Any] | str | None` | 构造时必需。 |

### 公共方法

该类型除了 dataclass/protocol 结构外不暴露公共方法。

### 协作关系与关联类型

- `ProviderTurnResult`
- `TokenUsage`
- `ToolExecutionOutcome`
- `Broth`

### 最小调用示例

```python
ToolCall(call_id=..., name=..., arguments=...)
```

## ProviderTurnResult

用于面向 provider 的执行循环，以及统一的消息/工具执行数据类型的 dataclass 载荷。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/unchain/runtime/engine.py:74` |
| 模块职责 | 面向 provider 的执行循环，以及统一的消息/工具执行数据类型。 |
| 继承/协议 | `-` |
| 导出状态 | 通过所属子包 `__init__` 导出。 |
| 对象类型 | Dataclass；公开或包内可见。 |

### 字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `assistant_messages` | `list[dict[str, Any]]` | 构造时必需。 |
| `tool_calls` | `list[ToolCall]` | 构造时必需。 |
| `final_text` | `str` | 默认值：`''`。 |
| `response_id` | `str | None` | 默认值：`None`。 |
| `reasoning_items` | `list[dict[str, Any]] | None` | 默认值：`None`。 |
| `consumed_tokens` | `int` | 默认值：`0`。 |
| `input_tokens` | `int` | 默认值：`0`。 |
| `output_tokens` | `int` | 默认值：`0`。 |

### 公共方法

该类型除了 dataclass/protocol 结构外不暴露公共方法。

### 协作关系与关联类型

- `ToolCall`
- `TokenUsage`
- `ToolExecutionOutcome`
- `Broth`

### 最小调用示例

```python
ProviderTurnResult(assistant_messages=..., tool_calls=..., final_text=..., response_id=...)
```

## TokenUsage

用于面向 provider 的执行循环，以及统一的消息/工具执行数据类型的 dataclass 载荷。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/unchain/runtime/engine.py:86` |
| 模块职责 | 面向 provider 的执行循环，以及统一的消息/工具执行数据类型。 |
| 继承/协议 | `-` |
| 导出状态 | 未导出，应视为实现细节。 |
| 对象类型 | Dataclass；公开或包内可见。 |

### 字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `consumed_tokens` | `int` | 默认值：`0`。 |
| `input_tokens` | `int` | 默认值：`0`。 |
| `output_tokens` | `int` | 默认值：`0`。 |

### 公共方法

该类型除了 dataclass/protocol 结构外不暴露公共方法。

### 协作关系与关联类型

- `ToolCall`
- `ProviderTurnResult`
- `ToolExecutionOutcome`
- `Broth`

### 最小调用示例

```python
TokenUsage(consumed_tokens=..., input_tokens=..., output_tokens=...)
```

## ToolExecutionOutcome

用于面向 provider 的执行循环，以及统一的消息/工具执行数据类型的 dataclass 载荷。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/unchain/runtime/engine.py:93` |
| 模块职责 | 面向 provider 的执行循环，以及统一的消息/工具执行数据类型。 |
| 继承/协议 | `-` |
| 导出状态 | 通过所属子包 `__init__` 导出。 |
| 对象类型 | Dataclass；公开或包内可见。 |

### 字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `result_messages` | `list[dict[str, Any]]` | 构造时必需。 |
| `should_observe` | `bool` | 默认值：`False`。 |
| `awaiting_human_input` | `bool` | 默认值：`False`。 |
| `human_input_request` | `HumanInputRequest | None` | 默认值：`None`。 |

### 公共方法

该类型除了 dataclass/protocol 结构外不暴露公共方法。

### 协作关系与关联类型

- `ToolCall`
- `ProviderTurnResult`
- `TokenUsage`
- `Broth`

### 最小调用示例

```python
ToolExecutionOutcome(result_messages=..., should_observe=..., awaiting_human_input=..., human_input_request=...)
```

## Broth

规范化的 provider/runtime 主循环，负责准备上下文、执行工具、处理暂停，并返回消息与 bundle。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/unchain/runtime/engine.py:103` |
| 模块职责 | 面向 provider 的执行循环，以及统一的消息/工具执行数据类型。 |
| 继承/协议 | `-` |
| 导出状态 | 通过所属子包 `__init__` 导出。 |
| 对象类型 | 类；公开或包内可见。 |

### 构造表面

该类主要通过构造函数定义必需输入和校验逻辑。

- `__init__(self, *, provider: str | None=None, model: str | None=None, api_key: str | None=None, memory_manager: MemoryManager | None=None, toolkit_catalog_config: ToolkitCatalogConfig | dict[str, Any] | None=None)`

### 属性

- `@property toolkit`: Return a merged view of all registered toolkits.
- `@property max_context_window_tokens`: Return the context window token limit.

### 公共方法

#### `__init__(self, *, provider: str | None=None, model: str | None=None, api_key: str | None=None, memory_manager: MemoryManager | None=None, toolkit_catalog_config: ToolkitCatalogConfig | dict[str, Any] | None=None)`

初始化实例，并在类有约束时校验或强制转换构造参数。

- 类型：构造函数
- 定义位置：`src/unchain/runtime/engine.py:104`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `add_toolkit(self, tk: BaseToolkit)`

Append a toolkit to the agent's toolkit list.

- 类型：方法
- 定义位置：`src/unchain/runtime/engine.py:262`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `remove_toolkit(self, tk: BaseToolkit)`

Remove a toolkit from the agent's toolkit list.

- 类型：方法
- 定义位置：`src/unchain/runtime/engine.py:266`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `run(self, messages, payload: dict[str, Any] | None=None, response_format: ResponseFormat | None=None, callback: Callable[[dict[str, Any]], None] | None=None, verbose: bool=False, max_iterations: int | None=None, previous_response_id: str | None=None, on_tool_confirm: Callable | None=None, on_continuation_request: Callable | None=None, session_id: str | None=None, memory_namespace: str | None=None)`

`Broth` 对外暴露的方法 `run`。

- 类型：方法
- 定义位置：`src/unchain/runtime/engine.py:1737`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `resume_human_input(self, *, conversation: list[dict[str, Any]], continuation: dict[str, Any], response: HumanInputResponse | dict[str, Any], payload: dict[str, Any] | None=None, response_format: ResponseFormat | None=None, callback: Callable[[dict[str, Any]], None] | None=None, verbose: bool=False, on_tool_confirm: Callable | None=None, on_continuation_request: Callable | None=None, session_id: str | None=None, memory_namespace: str | None=None)`

`Broth` 对外暴露的方法 `resume_human_input`。

- 类型：方法
- 定义位置：`src/unchain/runtime/engine.py:1826`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

### 生命周期与运行时角色

- 初始化时会加载 provider 默认 payload 与 model capability，建立计数器，并把 provider SDK import 延迟到真正执行时。
- `run()` 会规范化消息、注入 workspace pins、准备 memory context、抓取 provider turn、执行工具、运行 observation 并在每轮提交 memory。
- 当工具需要确认或人类输入时，runtime 会打包 continuation 状态并提前返回，后续通过 `resume_human_input()` 恢复。
- toolkit catalog runtime 通过 state token 暂存，因此恢复后的运行可继续看到相同的 active/managed toolkit 集。

### 协作关系与关联类型

- `ToolCall`
- `ProviderTurnResult`
- `TokenUsage`
- `ToolExecutionOutcome`

### 最小调用示例

```python
obj = Broth(...)
obj.add_toolkit(...)
```
