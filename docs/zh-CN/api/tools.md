# 工具系统 API 参考

覆盖 Tool 原语、Toolkit 容器、动态 catalog 以及 toolkit 发现/描述对象。

| 指标 | 值 |
| --- | --- |
| 类数量 | 14 |
| Dataclass | 10 |
| 协议 | 0 |
| 仅内部类型 | 0 |

## 覆盖地图

| 类 | 源码 | 导出 | 类型 |
| --- | --- | --- | --- |
| `ToolkitCatalogConfig` | `src/unchain/tools/catalog.py:34` | subpackage | dataclass |
| `ToolkitCatalogRuntime` | `src/unchain/tools/catalog.py:76` | subpackage | class |
| `ToolParameter` | `src/unchain/tools/models.py:155` | subpackage | dataclass |
| `ToolHistoryOptimizationContext` | `src/unchain/tools/models.py:178` | subpackage | dataclass |
| `NormalizedToolHistoryRecord` | `src/unchain/tools/models.py:191` | subpackage | dataclass |
| `ToolConfirmationRequest` | `src/unchain/tools/models.py:209` | subpackage | dataclass |
| `ToolConfirmationResponse` | `src/unchain/tools/models.py:233` | subpackage | dataclass |
| `ToolRegistryConfig` | `src/unchain/tools/registry.py:192` | subpackage | dataclass |
| `ToolDescriptor` | `src/unchain/tools/registry.py:222` | subpackage | dataclass |
| `IconDescriptor` | `src/unchain/tools/registry.py:246` | internal | dataclass |
| `ToolkitDescriptor` | `src/unchain/tools/registry.py:286` | subpackage | dataclass |
| `ToolkitRegistry` | `src/unchain/tools/registry.py:378` | subpackage | class |
| `Tool` | `src/unchain/tools/tool.py:16` | subpackage | class |
| `Toolkit` | `src/unchain/tools/toolkit.py:9` | subpackage | class |

### `src/unchain/tools/catalog.py`

在运行时列出、描述、激活受管 toolkit 的 catalog 层。

## ToolkitCatalogConfig

用于在运行时列出、描述、激活受管 toolkit 的 catalog 层的 dataclass 载荷。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/unchain/tools/catalog.py:34` |
| 模块职责 | 在运行时列出、描述、激活受管 toolkit 的 catalog 层。 |
| 继承/协议 | `-` |
| 导出状态 | 通过所属子包 `__init__` 导出。 |
| 对象类型 | Dataclass；公开或包内可见。 |

### 字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `managed_toolkit_ids` | `tuple[str, ...]` | 构造时必需。 |
| `always_active_toolkit_ids` | `tuple[str, ...]` | 构造时必需。 |
| `registry` | `ToolRegistryConfig` | 构造时必需。 |
| `readme_max_chars` | `int` | 构造时必需。 |

### 公共方法

#### `__init__(self, *, managed_toolkit_ids: tuple[str, ...] | list[str] | None, always_active_toolkit_ids: tuple[str, ...] | list[str] | None=None, registry: ToolRegistryConfig | dict[str, Any] | None=None, readme_max_chars: int=8000)`

初始化实例，并在类有约束时校验或强制转换构造参数。

- 类型：构造函数
- 定义位置：`src/unchain/tools/catalog.py:40`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `coerce(cls, value: ToolkitCatalogConfig | dict[str, Any] | None)`

`ToolkitCatalogConfig` 对外暴露的方法 `coerce`。

- 类型：方法
- 定义位置：`src/unchain/tools/catalog.py:66`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

### 协作关系与关联类型

- `ToolkitCatalogRuntime`

### 最小调用示例

```python
ToolkitCatalogConfig(managed_toolkit_ids=..., always_active_toolkit_ids=..., registry=..., readme_max_chars=...)
```

## ToolkitCatalogRuntime

对模型可见的运行时 toolkit，可列出、描述、激活和停用受管 toolkit，而不影响 eager toolkit。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/unchain/tools/catalog.py:76` |
| 模块职责 | 在运行时列出、描述、激活受管 toolkit 的 catalog 层。 |
| 继承/协议 | `Toolkit` |
| 导出状态 | 通过所属子包 `__init__` 导出。 |
| 对象类型 | 类；公开或包内可见。 |

### 构造表面

该类主要通过构造函数定义必需输入和校验逻辑。

- `__init__(self, *, config: ToolkitCatalogConfig, eager_toolkits: list[Toolkit])`

### 公共方法

#### `__init__(self, *, config: ToolkitCatalogConfig, eager_toolkits: list[Toolkit])`

初始化实例，并在类有约束时校验或强制转换构造参数。

- 类型：构造函数
- 定义位置：`src/unchain/tools/catalog.py:77`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `visible_toolkits(self)`

`ToolkitCatalogRuntime` 对外暴露的方法 `visible_toolkits`。

- 类型：方法
- 定义位置：`src/unchain/tools/catalog.py:147`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `active_toolkit_ids(self)`

`ToolkitCatalogRuntime` 对外暴露的方法 `active_toolkit_ids`。

- 类型：方法
- 定义位置：`src/unchain/tools/catalog.py:150`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `toolkit_list(self)`

`ToolkitCatalogRuntime` 对外暴露的方法 `toolkit_list`。

- 类型：方法
- 定义位置：`src/unchain/tools/catalog.py:219`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `toolkit_describe(self, toolkit_id: str, tool_name: str | None=None)`

`ToolkitCatalogRuntime` 对外暴露的方法 `toolkit_describe`。

- 类型：方法
- 定义位置：`src/unchain/tools/catalog.py:228`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `toolkit_activate(self, toolkit_id: str)`

`ToolkitCatalogRuntime` 对外暴露的方法 `toolkit_activate`。

- 类型：方法
- 定义位置：`src/unchain/tools/catalog.py:259`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `toolkit_deactivate(self, toolkit_id: str)`

`ToolkitCatalogRuntime` 对外暴露的方法 `toolkit_deactivate`。

- 类型：方法
- 定义位置：`src/unchain/tools/catalog.py:262`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `toolkit_list_active(self)`

`ToolkitCatalogRuntime` 对外暴露的方法 `toolkit_list_active`。

- 类型：方法
- 定义位置：`src/unchain/tools/catalog.py:292`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `build_continuation_state(self)`

`ToolkitCatalogRuntime` 对外暴露的方法 `build_continuation_state`。

- 类型：方法
- 定义位置：`src/unchain/tools/catalog.py:303`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `shutdown(self)`

`ToolkitCatalogRuntime` 对外暴露的方法 `shutdown`。

- 类型：方法
- 定义位置：`src/unchain/tools/catalog.py:313`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `to_summary(self)`

`ToolkitCatalogRuntime` 对外暴露的方法 `to_summary`。

- 类型：方法
- 定义位置：`src/unchain/tools/catalog.py:320`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

### 生命周期与运行时角色

- 初始化时会校验 managed toolkit 集、注册 catalog 控制工具，并预激活 always-on toolkit。
- 运行时通过 `toolkit_list()` 和 `toolkit_describe()` 暴露元数据，激活/停用只会修改 managed active 集。
- continuation helper 会序列化 state token，使暂停后的运行可以恢复同一个 catalog 实例。

### 协作关系与关联类型

- `ToolkitCatalogConfig`

### 最小调用示例

```python
obj = ToolkitCatalogRuntime(...)
obj.visible_toolkits(...)
```

### `src/unchain/tools/models.py`

为参数、历史压缩和确认流提供的小型支撑模型。

## ToolParameter

用于为参数、历史压缩和确认流提供的小型支撑模型的 dataclass 载荷。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/unchain/tools/models.py:155` |
| 模块职责 | 为参数、历史压缩和确认流提供的小型支撑模型。 |
| 继承/协议 | `-` |
| 导出状态 | 通过所属子包 `__init__` 导出。 |
| 对象类型 | Dataclass；公开或包内可见。 |

### 字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `name` | `str` | 构造时必需。 |
| `description` | `str` | 构造时必需。 |
| `type_` | `str` | 构造时必需。 |
| `required` | `bool` | 默认值：`False`。 |
| `pattern` | `str | None` | 默认值：`None`。 |
| `items` | `dict[str, Any] | None` | 默认值：`None`。 |

### 公共方法

#### `to_json(self)`

`ToolParameter` 对外暴露的方法 `to_json`。

- 类型：方法
- 定义位置：`src/unchain/tools/models.py:163`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

### 协作关系与关联类型

- `ToolHistoryOptimizationContext`
- `NormalizedToolHistoryRecord`
- `ToolConfirmationRequest`
- `ToolConfirmationResponse`

### 最小调用示例

```python
ToolParameter(name=..., description=..., type_=..., required=...)
```

## ToolHistoryOptimizationContext

用于为参数、历史压缩和确认流提供的小型支撑模型的 dataclass 载荷。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/unchain/tools/models.py:178` |
| 模块职责 | 为参数、历史压缩和确认流提供的小型支撑模型。 |
| 继承/协议 | `-` |
| 导出状态 | 通过所属子包 `__init__` 导出。 |
| 对象类型 | Dataclass；公开或包内可见。 |

### 字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `tool_name` | `str` | 构造时必需。 |
| `call_id` | `str` | 构造时必需。 |
| `kind` | `str` | 构造时必需。 |
| `provider` | `str` | 构造时必需。 |
| `session_id` | `str` | 构造时必需。 |
| `latest_messages` | `list[dict[str, Any]]` | 构造时必需。 |
| `max_chars` | `int` | 构造时必需。 |
| `preview_chars` | `int` | 构造时必需。 |
| `include_hash` | `bool` | 默认值：`True`。 |

### 公共方法

该类型除了 dataclass/protocol 结构外不暴露公共方法。

### 协作关系与关联类型

- `ToolParameter`
- `NormalizedToolHistoryRecord`
- `ToolConfirmationRequest`
- `ToolConfirmationResponse`

### 最小调用示例

```python
ToolHistoryOptimizationContext(tool_name=..., call_id=..., kind=..., provider=...)
```

## NormalizedToolHistoryRecord

用于为参数、历史压缩和确认流提供的小型支撑模型的 dataclass 载荷。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/unchain/tools/models.py:191` |
| 模块职责 | 为参数、历史压缩和确认流提供的小型支撑模型。 |
| 继承/协议 | `-` |
| 导出状态 | 通过所属子包 `__init__` 导出。 |
| 对象类型 | Dataclass；公开或包内可见。 |

### 字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `tool_name` | `str` | 构造时必需。 |
| `call_id` | `str` | 构造时必需。 |
| `kind` | `str` | 构造时必需。 |
| `payload` | `Any` | 构造时必需。 |
| `provider` | `str` | 构造时必需。 |
| `message_index` | `int` | 构造时必需。 |
| `location_type` | `str` | 构造时必需。 |
| `payload_format` | `str` | 构造时必需。 |
| `block_index` | `int | None` | 默认值：`None`。 |
| `part_index` | `int | None` | 默认值：`None`。 |
| `field_name` | `str | None` | 默认值：`None`。 |

### 公共方法

该类型除了 dataclass/protocol 结构外不暴露公共方法。

### 协作关系与关联类型

- `ToolParameter`
- `ToolHistoryOptimizationContext`
- `ToolConfirmationRequest`
- `ToolConfirmationResponse`

### 最小调用示例

```python
NormalizedToolHistoryRecord(tool_name=..., call_id=..., kind=..., payload=...)
```

## ToolConfirmationRequest

用于为参数、历史压缩和确认流提供的小型支撑模型的 dataclass 载荷。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/unchain/tools/models.py:209` |
| 模块职责 | 为参数、历史压缩和确认流提供的小型支撑模型。 |
| 继承/协议 | `-` |
| 导出状态 | 通过所属子包 `__init__` 导出。 |
| 对象类型 | Dataclass；公开或包内可见。 |

### 字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `tool_name` | `str` | 构造时必需。 |
| `call_id` | `str` | 构造时必需。 |
| `arguments` | `dict[str, Any]` | 构造时必需。 |
| `description` | `str` | 默认值：`''`。 |
| `interact_type` | `str` | 默认值：`'confirmation'`。 |
| `interact_config` | `dict[str, Any] | list[Any] | None` | 默认值：`None`。 |

### 公共方法

#### `to_dict(self)`

`ToolConfirmationRequest` 对外暴露的方法 `to_dict`。

- 类型：方法
- 定义位置：`src/unchain/tools/models.py:217`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

### 协作关系与关联类型

- `ToolParameter`
- `ToolHistoryOptimizationContext`
- `NormalizedToolHistoryRecord`
- `ToolConfirmationResponse`

### 最小调用示例

```python
ToolConfirmationRequest(tool_name=..., call_id=..., arguments=..., description=...)
```

## ToolConfirmationResponse

用于为参数、历史压缩和确认流提供的小型支撑模型的 dataclass 载荷。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/unchain/tools/models.py:233` |
| 模块职责 | 为参数、历史压缩和确认流提供的小型支撑模型。 |
| 继承/协议 | `-` |
| 导出状态 | 通过所属子包 `__init__` 导出。 |
| 对象类型 | Dataclass；公开或包内可见。 |

### 字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `approved` | `bool` | 默认值：`True`。 |
| `modified_arguments` | `dict[str, Any] | None` | 默认值：`None`。 |
| `reason` | `str` | 默认值：`''`。 |

### 公共方法

#### `from_raw(cls, raw: bool | dict[str, Any] | 'ToolConfirmationResponse')`

`ToolConfirmationResponse` 对外暴露的方法 `from_raw`。

- 类型：方法
- 定义位置：`src/unchain/tools/models.py:239`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

### 协作关系与关联类型

- `ToolParameter`
- `ToolHistoryOptimizationContext`
- `NormalizedToolHistoryRecord`
- `ToolConfirmationRequest`

### 最小调用示例

```python
ToolConfirmationResponse(approved=..., modified_arguments=..., reason=...)
```

### `src/unchain/tools/registry.py`

负责 manifest 发现、元数据校验、icon 解析与 toolkit 实例化。

## ToolRegistryConfig

用于负责 manifest 发现、元数据校验、icon 解析与 toolkit 实例化的 dataclass 载荷。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/unchain/tools/registry.py:192` |
| 模块职责 | 负责 manifest 发现、元数据校验、icon 解析与 toolkit 实例化。 |
| 继承/协议 | `-` |
| 导出状态 | 通过所属子包 `__init__` 导出。 |
| 对象类型 | Dataclass；公开或包内可见。 |

### 字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `local_roots` | `tuple[str, ...]` | 默认值：`()`。 |
| `enabled_plugins` | `tuple[str, ...]` | 默认值：`()`。 |
| `include_builtin` | `bool` | 默认值：`True`。 |
| `validate` | `bool` | 默认值：`True`。 |

### 公共方法

#### `__init__(self, local_roots: Sequence[str | Path] | None=None, enabled_plugins: Sequence[str] | None=None, include_builtin: bool=True, validate: bool=True)`

初始化实例，并在类有约束时校验或强制转换构造参数。

- 类型：构造函数
- 定义位置：`src/unchain/tools/registry.py:198`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `coerce(cls, value: ToolRegistryConfig | dict[str, Any] | None)`

`ToolRegistryConfig` 对外暴露的方法 `coerce`。

- 类型：方法
- 定义位置：`src/unchain/tools/registry.py:211`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

### 协作关系与关联类型

- `ToolDescriptor`
- `IconDescriptor`
- `ToolkitDescriptor`
- `ToolkitRegistry`

### 最小调用示例

```python
ToolRegistryConfig(local_roots=..., enabled_plugins=..., include_builtin=..., validate=...)
```

## ToolDescriptor

用于负责 manifest 发现、元数据校验、icon 解析与 toolkit 实例化的 dataclass 载荷。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/unchain/tools/registry.py:222` |
| 模块职责 | 负责 manifest 发现、元数据校验、icon 解析与 toolkit 实例化。 |
| 继承/协议 | `-` |
| 导出状态 | 通过所属子包 `__init__` 导出。 |
| 对象类型 | Dataclass；公开或包内可见。 |

### 字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `name` | `str` | 构造时必需。 |
| `title` | `str` | 构造时必需。 |
| `description` | `str` | 构造时必需。 |
| `icon_path` | `Path | None` | 构造时必需。 |
| `icon` | `'IconDescriptor'` | 构造时必需。 |
| `hidden` | `bool` | 默认值：`False`。 |
| `requires_confirmation` | `bool` | 默认值：`False`。 |
| `observe` | `bool` | 默认值：`False`。 |

### 公共方法

#### `to_summary(self)`

`ToolDescriptor` 对外暴露的方法 `to_summary`。

- 类型：方法
- 定义位置：`src/unchain/tools/registry.py:232`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

### 协作关系与关联类型

- `ToolRegistryConfig`
- `IconDescriptor`
- `ToolkitDescriptor`
- `ToolkitRegistry`

### 最小调用示例

```python
ToolDescriptor(name=..., title=..., description=..., icon_path=...)
```

## IconDescriptor

用于负责 manifest 发现、元数据校验、icon 解析与 toolkit 实例化的 dataclass 载荷。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/unchain/tools/registry.py:246` |
| 模块职责 | 负责 manifest 发现、元数据校验、icon 解析与 toolkit 实例化。 |
| 继承/协议 | `-` |
| 导出状态 | 未导出，应视为实现细节。 |
| 对象类型 | Dataclass；公开或包内可见。 |

### 字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `type` | `str` | 构造时必需。 |
| `path` | `Path | None` | 默认值：`None`。 |
| `name` | `str | None` | 默认值：`None`。 |
| `color` | `str | None` | 默认值：`None`。 |
| `background_color` | `str | None` | 默认值：`None`。 |

### 公共方法

#### `from_file(cls, path: Path)`

`IconDescriptor` 对外暴露的方法 `from_file`。

- 类型：方法
- 定义位置：`src/unchain/tools/registry.py:254`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `from_builtin(cls, name: str, color: str, background_color: str)`

`IconDescriptor` 对外暴露的方法 `from_builtin`。

- 类型：方法
- 定义位置：`src/unchain/tools/registry.py:258`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `to_summary(self)`

`IconDescriptor` 对外暴露的方法 `to_summary`。

- 类型：方法
- 定义位置：`src/unchain/tools/registry.py:271`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

### 协作关系与关联类型

- `ToolRegistryConfig`
- `ToolDescriptor`
- `ToolkitDescriptor`
- `ToolkitRegistry`

### 最小调用示例

```python
IconDescriptor(type=..., path=..., name=..., color=...)
```

## ToolkitDescriptor

用于负责 manifest 发现、元数据校验、icon 解析与 toolkit 实例化的 dataclass 载荷。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/unchain/tools/registry.py:286` |
| 模块职责 | 负责 manifest 发现、元数据校验、icon 解析与 toolkit 实例化。 |
| 继承/协议 | `-` |
| 导出状态 | 通过所属子包 `__init__` 导出。 |
| 对象类型 | Dataclass；公开或包内可见。 |

### 字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | `str` | 构造时必需。 |
| `name` | `str` | 构造时必需。 |
| `description` | `str` | 构造时必需。 |
| `factory` | `str` | 构造时必需。 |
| `version` | `str | None` | 构造时必需。 |
| `tags` | `tuple[str, ...]` | 构造时必需。 |
| `manifest_path` | `Path` | 构造时必需。 |
| `root_path` | `Path` | 构造时必需。 |
| `readme_path` | `Path` | 构造时必需。 |
| `icon_path` | `Path | None` | 构造时必需。 |
| `icon` | `IconDescriptor` | 构造时必需。 |
| `source` | `str` | 构造时必需。 |
| `display_category` | `str | None` | 默认值：`None`。 |
| `display_order` | `int` | 默认值：`0`。 |
| `hidden` | `bool` | 默认值：`False`。 |
| `compat_python` | `str | None` | 默认值：`None`。 |
| `compat_unchain` | `str | None` | 默认值：`None`。 |
| `tools` | `dict[str, ToolDescriptor]` | 默认值：`field(default_factory=dict)`。 |
| `import_roots` | `tuple[Path, ...]` | 默认值：`field(default_factory=tuple, repr=False)`。 |

### 公共方法

#### `sorted_tools(self)`

`ToolkitDescriptor` 对外暴露的方法 `sorted_tools`。

- 类型：方法
- 定义位置：`src/unchain/tools/registry.py:307`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `to_summary(self, *, include_tools: bool=True)`

`ToolkitDescriptor` 对外暴露的方法 `to_summary`。

- 类型：方法
- 定义位置：`src/unchain/tools/registry.py:313`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `to_metadata(self, *, include_tools: bool=True)`

`ToolkitDescriptor` 对外暴露的方法 `to_metadata`。

- 类型：方法
- 定义位置：`src/unchain/tools/registry.py:342`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

### 协作关系与关联类型

- `ToolRegistryConfig`
- `ToolDescriptor`
- `IconDescriptor`
- `ToolkitRegistry`

### 最小调用示例

```python
ToolkitDescriptor(id=..., name=..., description=..., factory=...)
```

## ToolkitRegistry

负责读取 toolkit manifest、解析资源并实例化 builtin/local/plugin toolkit 的发现与校验服务。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/unchain/tools/registry.py:378` |
| 模块职责 | 负责 manifest 发现、元数据校验、icon 解析与 toolkit 实例化。 |
| 继承/协议 | `-` |
| 导出状态 | 通过所属子包 `__init__` 导出。 |
| 对象类型 | 类；公开或包内可见。 |

### 构造表面

该类主要通过构造函数定义必需输入和校验逻辑。

- `__init__(self, config: ToolRegistryConfig | dict[str, Any] | None=None)`

### 属性

- `@property toolkits`: 公开属性访问器。

### 公共方法

#### `__init__(self, config: ToolRegistryConfig | dict[str, Any] | None=None)`

初始化实例，并在类有约束时校验或强制转换构造参数。

- 类型：构造函数
- 定义位置：`src/unchain/tools/registry.py:381`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `list_toolkits(self, *, include_tools: bool=True)`

`ToolkitRegistry` 对外暴露的方法 `list_toolkits`。

- 类型：方法
- 定义位置：`src/unchain/tools/registry.py:390`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `get(self, toolkit_id: str)`

`ToolkitRegistry` 对外暴露的方法 `get`。

- 类型：方法
- 定义位置：`src/unchain/tools/registry.py:396`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `require(self, toolkit_id: str)`

`ToolkitRegistry` 对外暴露的方法 `require`。

- 类型：方法
- 定义位置：`src/unchain/tools/registry.py:399`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `get_toolkit_metadata(self, toolkit_id: str, tool_name: str | None=None)`

`ToolkitRegistry` 对外暴露的方法 `get_toolkit_metadata`。

- 类型：方法
- 定义位置：`src/unchain/tools/registry.py:405`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `instantiate_toolkit(self, toolkit_id: str)`

`ToolkitRegistry` 对外暴露的方法 `instantiate_toolkit`。

- 类型：方法
- 定义位置：`src/unchain/tools/registry.py:419`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

### 生命周期与运行时角色

- 初始化时保存 registry 配置，并在默认情况下立即发现 descriptor。
- 发现流程会遍历 builtin manifest、配置的 local root 和 plugin entry point，并校验 manifest 元数据与运行时工具一致。
- 实例化流程负责导入 factory、创建运行时 toolkit，并返回可执行的 `Toolkit`。

### 协作关系与关联类型

- `ToolRegistryConfig`
- `ToolDescriptor`
- `IconDescriptor`
- `ToolkitDescriptor`

### 最小调用示例

```python
obj = ToolkitRegistry(...)
obj.list_toolkits(...)
```

### `src/unchain/tools/tool.py`

核心 Tool 封装，负责携带元数据并执行规范化后的参数。

## Tool

携带元数据的 callable 包装对象，是最小可执行工具单元。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/unchain/tools/tool.py:16` |
| 模块职责 | 核心 Tool 封装，负责携带元数据并执行规范化后的参数。 |
| 继承/协议 | `-` |
| 导出状态 | 通过所属子包 `__init__` 导出。 |
| 对象类型 | 类；公开或包内可见。 |

### 构造表面

该类主要通过构造函数定义必需输入和校验逻辑。

- `__init__(self, name: str | Callable[..., Any]='', description: str='', func: Callable[..., Any] | None=None, parameters: list[ToolParameter | dict[str, Any]] | None=None, observe: bool=False, requires_confirmation: bool=False, history_arguments_optimizer: HistoryPayloadOptimizer | None=None, history_result_optimizer: HistoryPayloadOptimizer | None=None)`

### 公共方法

#### `__init__(self, name: str | Callable[..., Any]='', description: str='', func: Callable[..., Any] | None=None, parameters: list[ToolParameter | dict[str, Any]] | None=None, observe: bool=False, requires_confirmation: bool=False, history_arguments_optimizer: HistoryPayloadOptimizer | None=None, history_result_optimizer: HistoryPayloadOptimizer | None=None)`

初始化实例，并在类有约束时校验或强制转换构造参数。

- 类型：构造函数
- 定义位置：`src/unchain/tools/tool.py:17`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `from_callable(cls, func: Callable[..., Any], *, name: str | None=None, description: str | None=None, parameters: list[ToolParameter | dict[str, Any]] | None=None, observe: bool=False, requires_confirmation: bool=False, history_arguments_optimizer: HistoryPayloadOptimizer | None=None, history_result_optimizer: HistoryPayloadOptimizer | None=None)`

`Tool` 对外暴露的方法 `from_callable`。

- 类型：方法
- 定义位置：`src/unchain/tools/tool.py:71`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `to_json(self)`

`Tool` 对外暴露的方法 `to_json`。

- 类型：方法
- 定义位置：`src/unchain/tools/tool.py:147`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `execute(self, arguments: dict[str, Any] | str | None)`

`Tool` 对外暴露的方法 `execute`。

- 类型：方法
- 定义位置：`src/unchain/tools/tool.py:167`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

### 协作关系与关联类型

- `ToolkitCatalogConfig`
- `ToolkitCatalogRuntime`
- `ToolParameter`
- `ToolHistoryOptimizationContext`
- `NormalizedToolHistoryRecord`

### 最小调用示例

```python
obj = Tool(...)
obj.from_callable(...)
```

### `src/unchain/tools/toolkit.py`

Tool 容器与注册表面，被 runtime 与 toolkit 实现共同使用。

## Toolkit

以字典方式管理 `Tool` 的容器，提供注册、查找、执行和 shutdown 辅助能力。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/unchain/tools/toolkit.py:9` |
| 模块职责 | Tool 容器与注册表面，被 runtime 与 toolkit 实现共同使用。 |
| 继承/协议 | `-` |
| 导出状态 | 通过所属子包 `__init__` 导出。 |
| 对象类型 | 类；公开或包内可见。 |

### 构造表面

该类主要通过构造函数定义必需输入和校验逻辑。

- `__init__(self, tools: dict[str, Tool] | None=None)`

### 公共方法

#### `__init__(self, tools: dict[str, Tool] | None=None)`

初始化实例，并在类有约束时校验或强制转换构造参数。

- 类型：构造函数
- 定义位置：`src/unchain/tools/toolkit.py:10`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `register(self, tool_obj: Tool | Callable[..., Any], *, observe: bool | None=None, requires_confirmation: bool | None=None, name: str | None=None, description: str | None=None, parameters: list[ToolParameter | dict[str, Any]] | None=None, history_arguments_optimizer: HistoryPayloadOptimizer | None=None, history_result_optimizer: HistoryPayloadOptimizer | None=None)`

`Toolkit` 对外暴露的方法 `register`。

- 类型：方法
- 定义位置：`src/unchain/tools/toolkit.py:16`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `register_many(self, *tool_objs: Tool | Callable[..., Any])`

`Toolkit` 对外暴露的方法 `register_many`。

- 类型：方法
- 定义位置：`src/unchain/tools/toolkit.py:62`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `tool(self, func: Callable[..., Any] | None=None, *, observe: bool=False, requires_confirmation: bool=False, name: str | None=None, description: str | None=None, parameters: list[ToolParameter | dict[str, Any]] | None=None, history_arguments_optimizer: HistoryPayloadOptimizer | None=None, history_result_optimizer: HistoryPayloadOptimizer | None=None)`

`Toolkit` 对外暴露的方法 `tool`。

- 类型：方法
- 定义位置：`src/unchain/tools/toolkit.py:68`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `get(self, function_name: str)`

`Toolkit` 对外暴露的方法 `get`。

- 类型：方法
- 定义位置：`src/unchain/tools/toolkit.py:106`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `execute(self, function_name: str, arguments: dict[str, Any] | str | None)`

`Toolkit` 对外暴露的方法 `execute`。

- 类型：方法
- 定义位置：`src/unchain/tools/toolkit.py:109`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `to_json(self)`

`Toolkit` 对外暴露的方法 `to_json`。

- 类型：方法
- 定义位置：`src/unchain/tools/toolkit.py:115`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `shutdown(self)`

`Toolkit` 对外暴露的方法 `shutdown`。

- 类型：方法
- 定义位置：`src/unchain/tools/toolkit.py:118`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

### 协作关系与关联类型

- `ToolkitCatalogConfig`
- `ToolkitCatalogRuntime`
- `ToolParameter`
- `ToolHistoryOptimizationContext`
- `NormalizedToolHistoryRecord`

### 最小调用示例

```python
obj = Toolkit(...)
obj.register(...)
```
