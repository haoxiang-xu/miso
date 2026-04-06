# Input、Workspace 与 Schema 参考

覆盖结构化人类输入模型、workspace pin/syntax 对象以及结构化输出 schema。

| 指标 | 值 |
| --- | --- |
| 类数量 | 7 |
| Dataclass | 6 |
| 协议 | 0 |
| 仅内部类型 | 0 |

## 覆盖地图

| 类 | 源码 | 导出 | 类型 |
| --- | --- | --- | --- |
| `HumanInputOption` | `src/unchain/input/human_input.py:61` | subpackage | dataclass |
| `HumanInputRequest` | `src/unchain/input/human_input.py:89` | subpackage | dataclass |
| `HumanInputResponse` | `src/unchain/input/human_input.py:225` | subpackage | dataclass |
| `ResponseFormat` | `src/unchain/schemas/response.py:7` | subpackage | class |
| `WorkspacePinExecutionContext` | `src/unchain/workspace/pins.py:35` | subpackage | dataclass |
| `ParsedSyntaxTree` | `src/unchain/workspace/syntax.py:215` | internal | dataclass |
| `DeclarationCandidate` | `src/unchain/workspace/syntax.py:228` | internal | dataclass |

### `src/unchain/input/human_input.py`

ask-user 流程使用的结构化问题/响应模型。

## HumanInputOption

用于ask-user 流程使用的结构化问题/响应模型的 dataclass 载荷。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/unchain/input/human_input.py:61` |
| 模块职责 | ask-user 流程使用的结构化问题/响应模型。 |
| 继承/协议 | `-` |
| 导出状态 | 通过所属子包 `__init__` 导出。 |
| 对象类型 | Dataclass；公开或包内可见。 |

### 字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `label` | `str` | 构造时必需。 |
| `value` | `str` | 构造时必需。 |
| `description` | `str` | 默认值：`''`。 |

### 公共方法

#### `from_raw(cls, raw: Any)`

`HumanInputOption` 对外暴露的方法 `from_raw`。

- 类型：方法
- 定义位置：`src/unchain/input/human_input.py:67`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `to_dict(self)`

`HumanInputOption` 对外暴露的方法 `to_dict`。

- 类型：方法
- 定义位置：`src/unchain/input/human_input.py:80`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

### 协作关系与关联类型

- `HumanInputRequest`
- `HumanInputResponse`

### 最小调用示例

```python
HumanInputOption(label=..., value=..., description=...)
```

## HumanInputRequest

用于ask-user 流程使用的结构化问题/响应模型的 dataclass 载荷。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/unchain/input/human_input.py:89` |
| 模块职责 | ask-user 流程使用的结构化问题/响应模型。 |
| 继承/协议 | `-` |
| 导出状态 | 通过所属子包 `__init__` 导出。 |
| 对象类型 | Dataclass；公开或包内可见。 |

### 字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `request_id` | `str` | 构造时必需。 |
| `kind` | `Literal['selector']` | 构造时必需。 |
| `title` | `str` | 构造时必需。 |
| `question` | `str` | 构造时必需。 |
| `selection_mode` | `Literal['single', 'multiple']` | 构造时必需。 |
| `options` | `list[HumanInputOption]` | 构造时必需。 |
| `allow_other` | `bool` | 默认值：`False`。 |
| `other_label` | `str` | 默认值：`'Other'`。 |
| `other_placeholder` | `str` | 默认值：`''`。 |
| `min_selected` | `int | None` | 默认值：`None`。 |
| `max_selected` | `int | None` | 默认值：`None`。 |

### 公共方法

#### `from_tool_arguments(cls, arguments: dict[str, Any] | str | None, *, request_id: str)`

`HumanInputRequest` 对外暴露的方法 `from_tool_arguments`。

- 类型：方法
- 定义位置：`src/unchain/input/human_input.py:103`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `from_dict(cls, raw: Any)`

`HumanInputRequest` 对外暴露的方法 `from_dict`。

- 类型：方法
- 定义位置：`src/unchain/input/human_input.py:180`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `to_dict(self)`

`HumanInputRequest` 对外暴露的方法 `to_dict`。

- 类型：方法
- 定义位置：`src/unchain/input/human_input.py:202`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `allowed_values(self)`

`HumanInputRequest` 对外暴露的方法 `allowed_values`。

- 类型：方法
- 定义位置：`src/unchain/input/human_input.py:217`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

### 协作关系与关联类型

- `HumanInputOption`
- `HumanInputResponse`

### 最小调用示例

```python
HumanInputRequest(request_id=..., kind=..., title=..., question=...)
```

## HumanInputResponse

用于ask-user 流程使用的结构化问题/响应模型的 dataclass 载荷。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/unchain/input/human_input.py:225` |
| 模块职责 | ask-user 流程使用的结构化问题/响应模型。 |
| 继承/协议 | `-` |
| 导出状态 | 通过所属子包 `__init__` 导出。 |
| 对象类型 | Dataclass；公开或包内可见。 |

### 字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `request_id` | `str` | 构造时必需。 |
| `selected_values` | `list[str]` | 构造时必需。 |
| `other_text` | `str | None` | 默认值：`None`。 |

### 公共方法

#### `from_raw(cls, raw: Any, *, request: HumanInputRequest)`

`HumanInputResponse` 对外暴露的方法 `from_raw`。

- 类型：方法
- 定义位置：`src/unchain/input/human_input.py:231`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `to_dict(self)`

`HumanInputResponse` 对外暴露的方法 `to_dict`。

- 类型：方法
- 定义位置：`src/unchain/input/human_input.py:295`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `to_tool_result(self)`

`HumanInputResponse` 对外暴露的方法 `to_tool_result`。

- 类型：方法
- 定义位置：`src/unchain/input/human_input.py:302`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

### 协作关系与关联类型

- `HumanInputOption`
- `HumanInputRequest`

### 最小调用示例

```python
HumanInputResponse(request_id=..., selected_values=..., other_text=...)
```

### `src/unchain/schemas/response.py`

结构化输出 schema 包装器，负责 provider 投影与解析。

## ResponseFormat

结构化输出 schema 包装器，可把同一 schema 投影到多个 provider 请求格式。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/unchain/schemas/response.py:7` |
| 模块职责 | 结构化输出 schema 包装器，负责 provider 投影与解析。 |
| 继承/协议 | `-` |
| 导出状态 | 通过所属子包 `__init__` 导出。 |
| 对象类型 | 类；公开或包内可见。 |

### 构造表面

该类主要通过构造函数定义必需输入和校验逻辑。

- `__init__(self, name: str, schema: dict[str, Any], required: list[str] | None=None, post_processor: Callable[[dict[str, Any]], dict[str, Any]] | None=None)`

### 公共方法

#### `__init__(self, name: str, schema: dict[str, Any], required: list[str] | None=None, post_processor: Callable[[dict[str, Any]], dict[str, Any]] | None=None)`

初始化实例，并在类有约束时校验或强制转换构造参数。

- 类型：构造函数
- 定义位置：`src/unchain/schemas/response.py:10`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `to_openai(self)`

`ResponseFormat` 对外暴露的方法 `to_openai`。

- 类型：方法
- 定义位置：`src/unchain/schemas/response.py:27`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `to_ollama(self)`

`ResponseFormat` 对外暴露的方法 `to_ollama`。

- 类型：方法
- 定义位置：`src/unchain/schemas/response.py:34`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `to_anthropic(self)`

Return a system-prompt suffix that instructs Claude to output JSON.

- 类型：方法
- 定义位置：`src/unchain/schemas/response.py:38`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `to_gemini(self)`

Return Gemini-compatible structured output config.

- 类型：方法
- 定义位置：`src/unchain/schemas/response.py:46`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。
- 补充：Returns a dict with ``response_mime_type`` and ``response_schema``
suitable for passing into Gemini's ``generation_config``.

#### `parse(self, content: str | dict[str, Any])`

`ResponseFormat` 对外暴露的方法 `parse`。

- 类型：方法
- 定义位置：`src/unchain/schemas/response.py:57`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

### 协作关系与关联类型

- `HumanInputOption`
- `HumanInputRequest`
- `HumanInputResponse`
- `WorkspacePinExecutionContext`
- `ParsedSyntaxTree`

### 最小调用示例

```python
obj = ResponseFormat(...)
obj.to_openai(...)
```

### `src/unchain/workspace/pins.py`

pinned file context 使用的执行上下文与重定位辅助逻辑。

## WorkspacePinExecutionContext

用于pinned file context 使用的执行上下文与重定位辅助逻辑的 dataclass 载荷。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/unchain/workspace/pins.py:35` |
| 模块职责 | pinned file context 使用的执行上下文与重定位辅助逻辑。 |
| 继承/协议 | `-` |
| 导出状态 | 通过所属子包 `__init__` 导出。 |
| 对象类型 | Dataclass；公开或包内可见。 |

### 字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `session_id` | `str` | 构造时必需。 |
| `session_store` | `SessionStore` | 构造时必需。 |

### 公共方法

该类型除了 dataclass/protocol 结构外不暴露公共方法。

### 协作关系与关联类型

- `HumanInputOption`
- `HumanInputRequest`
- `HumanInputResponse`
- `ResponseFormat`
- `ParsedSyntaxTree`

### 最小调用示例

```python
WorkspacePinExecutionContext(session_id=..., session_store=...)
```

### `src/unchain/workspace/syntax.py`

workspace toolkit 共用的语法树解析输出对象。

## ParsedSyntaxTree

用于workspace toolkit 共用的语法树解析输出对象的 dataclass 载荷。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/unchain/workspace/syntax.py:215` |
| 模块职责 | workspace toolkit 共用的语法树解析输出对象。 |
| 继承/协议 | `-` |
| 导出状态 | 未导出，应视为实现细节。 |
| 对象类型 | Dataclass；公开或包内可见。 |

### 字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `language` | `str` | 构造时必需。 |
| `source_bytes` | `bytes` | 构造时必需。 |
| `tree` | `Any` | 构造时必需。 |
| `parser` | `str` | 默认值：`PARSER_NAME`。 |
| `tree_kind` | `str` | 默认值：`TREE_KIND`。 |

### 属性

- `@property root_node`: 公开属性访问器。

### 公共方法

该类型除了 dataclass/protocol 结构外不暴露公共方法。

### 协作关系与关联类型

- `DeclarationCandidate`

### 最小调用示例

```python
ParsedSyntaxTree(language=..., source_bytes=..., tree=..., parser=...)
```

## DeclarationCandidate

用于workspace toolkit 共用的语法树解析输出对象的 dataclass 载荷。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/unchain/workspace/syntax.py:228` |
| 模块职责 | workspace toolkit 共用的语法树解析输出对象。 |
| 继承/协议 | `-` |
| 导出状态 | 未导出，应视为实现细节。 |
| 对象类型 | Dataclass；公开或包内可见。 |

### 字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `language` | `str` | 构造时必需。 |
| `type` | `str` | 构造时必需。 |
| `name` | `str` | 构造时必需。 |
| `start_line` | `int` | 构造时必需。 |
| `end_line` | `int` | 构造时必需。 |

### 公共方法

该类型除了 dataclass/protocol 结构外不暴露公共方法。

### 协作关系与关联类型

- `ParsedSyntaxTree`

### 最小调用示例

```python
DeclarationCandidate(language=..., type=..., name=..., start_line=...)
```
