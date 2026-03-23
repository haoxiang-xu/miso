# Pupu 子系统参考

覆盖 Pupu 子系统中的目录导入、安装实例持久化、runtime 挂载与 MCP 管理对象。

| 指标 | 值 |
| --- | --- |
| 类数量 | 20 |
| Dataclass | 10 |
| 协议 | 0 |
| 仅内部类型 | 0 |

## 覆盖地图

| 类 | 源码 | 导出 | 类型 |
| --- | --- | --- | --- |
| `ToolPreview` | `src/miso/pupu/models.py:46` | subpackage | dataclass |
| `FieldSpec` | `src/miso/pupu/models.py:67` | subpackage | dataclass |
| `InstallProfile` | `src/miso/pupu/models.py:101` | subpackage | dataclass |
| `ConnectionIssue` | `src/miso/pupu/models.py:141` | subpackage | dataclass |
| `ConnectionTestResult` | `src/miso/pupu/models.py:163` | subpackage | dataclass |
| `DraftEntry` | `src/miso/pupu/models.py:205` | subpackage | dataclass |
| `MCPImportDraft` | `src/miso/pupu/models.py:251` | subpackage | dataclass |
| `CatalogEntry` | `src/miso/pupu/models.py:286` | subpackage | dataclass |
| `InstalledServer` | `src/miso/pupu/models.py:348` | subpackage | dataclass |
| `GitHubRepositoryClient` | `src/miso/pupu/services.py:220` | subpackage | class |
| `CatalogService` | `src/miso/pupu/services.py:254` | subpackage | class |
| `ImportService` | `src/miso/pupu/services.py:373` | subpackage | class |
| `NamespacedToolkit` | `src/miso/pupu/services.py:646` | subpackage | class |
| `MCPTestService` | `src/miso/pupu/services.py:685` | subpackage | class |
| `ManagedRuntimeHandle` | `src/miso/pupu/services.py:885` | internal | dataclass |
| `MCPRuntimeManager` | `src/miso/pupu/services.py:892` | subpackage | class |
| `PupuMCPService` | `src/miso/pupu/services.py:954` | subpackage | class |
| `FileCatalogCache` | `src/miso/pupu/stores.py:19` | subpackage | class |
| `FileInstalledServerStore` | `src/miso/pupu/stores.py:36` | subpackage | class |
| `InMemorySecretStore` | `src/miso/pupu/stores.py:79` | subpackage | class |

### `src/miso/pupu/models.py`

Pupu 目录、草稿、安装配置与已安装 server 载荷 dataclass。

## ToolPreview

用于Pupu 目录、草稿、安装配置与已安装 server 载荷 dataclass的 dataclass 载荷。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/miso/pupu/models.py:46` |
| 模块职责 | Pupu 目录、草稿、安装配置与已安装 server 载荷 dataclass。 |
| 继承/协议 | `-` |
| 导出状态 | 通过所属子包 `__init__` 导出。 |
| 对象类型 | Dataclass；公开或包内可见。 |

### 字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `name` | `str` | 构造时必需。 |
| `description` | `str` | 默认值：`''`。 |

### 公共方法

#### `to_dict(self)`

`ToolPreview` 对外暴露的方法 `to_dict`。

- 类型：方法
- 定义位置：`src/miso/pupu/models.py:50`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `from_dict(cls, value: dict[str, Any] | str)`

`ToolPreview` 对外暴露的方法 `from_dict`。

- 类型：方法
- 定义位置：`src/miso/pupu/models.py:57`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

### 协作关系与关联类型

- `FieldSpec`
- `InstallProfile`
- `ConnectionIssue`
- `ConnectionTestResult`
- `DraftEntry`

### 最小调用示例

```python
ToolPreview(name=..., description=...)
```

## FieldSpec

用于Pupu 目录、草稿、安装配置与已安装 server 载荷 dataclass的 dataclass 载荷。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/miso/pupu/models.py:67` |
| 模块职责 | Pupu 目录、草稿、安装配置与已安装 server 载荷 dataclass。 |
| 继承/协议 | `-` |
| 导出状态 | 通过所属子包 `__init__` 导出。 |
| 对象类型 | Dataclass；公开或包内可见。 |

### 字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `key` | `str` | 构造时必需。 |
| `label` | `str` | 构造时必需。 |
| `kind` | `str` | 默认值：`'string'`。 |
| `required` | `bool` | 默认值：`False`。 |
| `secret` | `bool` | 默认值：`False`。 |
| `placeholder` | `str` | 默认值：`''`。 |
| `help_text` | `str` | 默认值：`''`。 |

### 公共方法

#### `to_dict(self)`

`FieldSpec` 对外暴露的方法 `to_dict`。

- 类型：方法
- 定义位置：`src/miso/pupu/models.py:76`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `from_dict(cls, value: dict[str, Any])`

`FieldSpec` 对外暴露的方法 `from_dict`。

- 类型：方法
- 定义位置：`src/miso/pupu/models.py:88`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

### 协作关系与关联类型

- `ToolPreview`
- `InstallProfile`
- `ConnectionIssue`
- `ConnectionTestResult`
- `DraftEntry`

### 最小调用示例

```python
FieldSpec(key=..., label=..., kind=..., required=...)
```

## InstallProfile

用于Pupu 目录、草稿、安装配置与已安装 server 载荷 dataclass的 dataclass 载荷。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/miso/pupu/models.py:101` |
| 模块职责 | Pupu 目录、草稿、安装配置与已安装 server 载荷 dataclass。 |
| 继承/协议 | `-` |
| 导出状态 | 通过所属子包 `__init__` 导出。 |
| 对象类型 | Dataclass；公开或包内可见。 |

### 字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | `str` | 构造时必需。 |
| `label` | `str` | 构造时必需。 |
| `runtime` | `str` | 构造时必需。 |
| `transport` | `str` | 构造时必需。 |
| `platforms` | `tuple[str, ...]` | 构造时必需。 |
| `fields` | `tuple[FieldSpec, ...]` | 构造时必需。 |
| `required_secrets` | `tuple[str, ...]` | 构造时必需。 |
| `default_values` | `dict[str, Any]` | 构造时必需。 |

### 公共方法

#### `to_dict(self)`

`InstallProfile` 对外暴露的方法 `to_dict`。

- 类型：方法
- 定义位置：`src/miso/pupu/models.py:111`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `from_dict(cls, value: dict[str, Any])`

`InstallProfile` 对外暴露的方法 `from_dict`。

- 类型：方法
- 定义位置：`src/miso/pupu/models.py:124`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

### 协作关系与关联类型

- `ToolPreview`
- `FieldSpec`
- `ConnectionIssue`
- `ConnectionTestResult`
- `DraftEntry`

### 最小调用示例

```python
InstallProfile(id=..., label=..., runtime=..., transport=...)
```

## ConnectionIssue

用于Pupu 目录、草稿、安装配置与已安装 server 载荷 dataclass的 dataclass 载荷。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/miso/pupu/models.py:141` |
| 模块职责 | Pupu 目录、草稿、安装配置与已安装 server 载荷 dataclass。 |
| 继承/协议 | `-` |
| 导出状态 | 通过所属子包 `__init__` 导出。 |
| 对象类型 | Dataclass；公开或包内可见。 |

### 字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `code` | `str` | 构造时必需。 |
| `message` | `str` | 构造时必需。 |
| `detail` | `str` | 默认值：`''`。 |

### 公共方法

#### `to_dict(self)`

`ConnectionIssue` 对外暴露的方法 `to_dict`。

- 类型：方法
- 定义位置：`src/miso/pupu/models.py:146`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `from_dict(cls, value: dict[str, Any])`

`ConnectionIssue` 对外暴露的方法 `from_dict`。

- 类型：方法
- 定义位置：`src/miso/pupu/models.py:154`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

### 协作关系与关联类型

- `ToolPreview`
- `FieldSpec`
- `InstallProfile`
- `ConnectionTestResult`
- `DraftEntry`

### 最小调用示例

```python
ConnectionIssue(code=..., message=..., detail=...)
```

## ConnectionTestResult

用于Pupu 目录、草稿、安装配置与已安装 server 载荷 dataclass的 dataclass 载荷。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/miso/pupu/models.py:163` |
| 模块职责 | Pupu 目录、草稿、安装配置与已安装 server 载荷 dataclass。 |
| 继承/协议 | `-` |
| 导出状态 | 通过所属子包 `__init__` 导出。 |
| 对象类型 | Dataclass；公开或包内可见。 |

### 字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `status` | `str` | 构造时必需。 |
| `phase` | `str` | 构造时必需。 |
| `summary` | `str` | 构造时必需。 |
| `tool_count` | `int` | 构造时必需。 |
| `tools` | `tuple[ToolPreview, ...]` | 构造时必需。 |
| `warnings` | `tuple[str, ...]` | 构造时必需。 |
| `errors` | `tuple[ConnectionIssue, ...]` | 构造时必需。 |

### 公共方法

#### `to_dict(self)`

`ConnectionTestResult` 对外暴露的方法 `to_dict`。

- 类型：方法
- 定义位置：`src/miso/pupu/models.py:172`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `from_dict(cls, value: dict[str, Any] | None)`

`ConnectionTestResult` 对外暴露的方法 `from_dict`。

- 类型：方法
- 定义位置：`src/miso/pupu/models.py:184`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

### 协作关系与关联类型

- `ToolPreview`
- `FieldSpec`
- `InstallProfile`
- `ConnectionIssue`
- `DraftEntry`

### 最小调用示例

```python
ConnectionTestResult(status=..., phase=..., summary=..., tool_count=...)
```

## DraftEntry

用于Pupu 目录、草稿、安装配置与已安装 server 载荷 dataclass的 dataclass 载荷。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/miso/pupu/models.py:205` |
| 模块职责 | Pupu 目录、草稿、安装配置与已安装 server 载荷 dataclass。 |
| 继承/协议 | `-` |
| 导出状态 | 通过所属子包 `__init__` 导出。 |
| 对象类型 | Dataclass；公开或包内可见。 |

### 字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `entry_id` | `str` | 构造时必需。 |
| `source_kind` | `str` | 构造时必需。 |
| `display_name` | `str` | 构造时必需。 |
| `profile_candidates` | `tuple[InstallProfile, ...]` | 构造时必需。 |
| `prefilled_config` | `dict[str, Any]` | 构造时必需。 |
| `required_fields` | `tuple[str, ...]` | 构造时必需。 |
| `required_secrets` | `tuple[str, ...]` | 构造时必需。 |
| `warnings` | `tuple[str, ...]` | 构造时必需。 |
| `catalog_entry_id` | `str | None` | 默认值：`None`。 |

### 公共方法

#### `to_dict(self)`

`DraftEntry` 对外暴露的方法 `to_dict`。

- 类型：方法
- 定义位置：`src/miso/pupu/models.py:216`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `from_dict(cls, value: dict[str, Any])`

`DraftEntry` 对外暴露的方法 `from_dict`。

- 类型：方法
- 定义位置：`src/miso/pupu/models.py:230`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

### 协作关系与关联类型

- `ToolPreview`
- `FieldSpec`
- `InstallProfile`
- `ConnectionIssue`
- `ConnectionTestResult`

### 最小调用示例

```python
DraftEntry(entry_id=..., source_kind=..., display_name=..., profile_candidates=...)
```

## MCPImportDraft

用于Pupu 目录、草稿、安装配置与已安装 server 载荷 dataclass的 dataclass 载荷。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/miso/pupu/models.py:251` |
| 模块职责 | Pupu 目录、草稿、安装配置与已安装 server 载荷 dataclass。 |
| 继承/协议 | `-` |
| 导出状态 | 通过所属子包 `__init__` 导出。 |
| 对象类型 | Dataclass；公开或包内可见。 |

### 字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `draft_id` | `str` | 构造时必需。 |
| `source_kind` | `str` | 构造时必需。 |
| `source_label` | `str` | 构造时必需。 |
| `warnings` | `tuple[str, ...]` | 构造时必需。 |
| `entries` | `tuple[DraftEntry, ...]` | 构造时必需。 |

### 公共方法

#### `to_dict(self)`

`MCPImportDraft` 对外暴露的方法 `to_dict`。

- 类型：方法
- 定义位置：`src/miso/pupu/models.py:258`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `create(cls, *, source_kind: str, source_label: str, warnings: list[str] | tuple[str, ...] | None=None, entries: list[DraftEntry] | tuple[DraftEntry, ...] | None=None)`

`MCPImportDraft` 对外暴露的方法 `create`。

- 类型：方法
- 定义位置：`src/miso/pupu/models.py:268`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

### 协作关系与关联类型

- `ToolPreview`
- `FieldSpec`
- `InstallProfile`
- `ConnectionIssue`
- `ConnectionTestResult`

### 最小调用示例

```python
MCPImportDraft(draft_id=..., source_kind=..., source_label=..., warnings=...)
```

## CatalogEntry

用于Pupu 目录、草稿、安装配置与已安装 server 载荷 dataclass的 dataclass 载荷。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/miso/pupu/models.py:286` |
| 模块职责 | Pupu 目录、草稿、安装配置与已安装 server 载荷 dataclass。 |
| 继承/协议 | `-` |
| 导出状态 | 通过所属子包 `__init__` 导出。 |
| 对象类型 | Dataclass；公开或包内可见。 |

### 字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | `str` | 构造时必需。 |
| `slug` | `str` | 构造时必需。 |
| `name` | `str` | 构造时必需。 |
| `publisher` | `str` | 构造时必需。 |
| `description` | `str` | 构造时必需。 |
| `icon_url` | `str` | 构造时必需。 |
| `verification` | `str` | 构造时必需。 |
| `source_url` | `str` | 构造时必需。 |
| `tags` | `tuple[str, ...]` | 构造时必需。 |
| `revoked` | `bool` | 构造时必需。 |
| `install_profiles` | `tuple[InstallProfile, ...]` | 构造时必需。 |
| `tool_preview` | `tuple[ToolPreview, ...]` | 构造时必需。 |
| `min_app_version` | `str | None` | 默认值：`None`。 |

### 公共方法

#### `to_dict(self)`

`CatalogEntry` 对外暴露的方法 `to_dict`。

- 类型：方法
- 定义位置：`src/miso/pupu/models.py:301`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `from_dict(cls, value: dict[str, Any])`

`CatalogEntry` 对外暴露的方法 `from_dict`。

- 类型：方法
- 定义位置：`src/miso/pupu/models.py:319`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

### 协作关系与关联类型

- `ToolPreview`
- `FieldSpec`
- `InstallProfile`
- `ConnectionIssue`
- `ConnectionTestResult`

### 最小调用示例

```python
CatalogEntry(id=..., slug=..., name=..., publisher=...)
```

## InstalledServer

用于Pupu 目录、草稿、安装配置与已安装 server 载荷 dataclass的 dataclass 载荷。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/miso/pupu/models.py:348` |
| 模块职责 | Pupu 目录、草稿、安装配置与已安装 server 载荷 dataclass。 |
| 继承/协议 | `-` |
| 导出状态 | 通过所属子包 `__init__` 导出。 |
| 对象类型 | Dataclass；公开或包内可见。 |

### 字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `instance_id` | `str` | 构造时必需。 |
| `instance_slug` | `str` | 构造时必需。 |
| `display_name` | `str` | 构造时必需。 |
| `source_kind` | `str` | 构造时必需。 |
| `runtime` | `str` | 构造时必需。 |
| `transport` | `str` | 构造时必需。 |
| `status` | `str` | 构造时必需。 |
| `enabled` | `bool` | 构造时必需。 |
| `normalized_config` | `dict[str, Any]` | 构造时必需。 |
| `required_secrets` | `tuple[str, ...]` | 构造时必需。 |
| `catalog_entry_id` | `str | None` | 默认值：`None`。 |
| `tool_count` | `int` | 默认值：`0`。 |
| `cached_tools` | `tuple[ToolPreview, ...]` | 默认值：`()`。 |
| `last_test_result` | `ConnectionTestResult | None` | 默认值：`None`。 |
| `updated_at` | `str` | 默认值：`''`。 |

### 属性

- `@property needs_secret`: 公开属性访问器。

### 公共方法

#### `with_updates(self, **kwargs: Any)`

`InstalledServer` 对外暴露的方法 `with_updates`。

- 类型：方法
- 定义位置：`src/miso/pupu/models.py:373`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `to_record(self)`

`InstalledServer` 对外暴露的方法 `to_record`。

- 类型：方法
- 定义位置：`src/miso/pupu/models.py:390`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `to_public_dict(self, *, configured_secrets: list[str] | tuple[str, ...] | None=None)`

`InstalledServer` 对外暴露的方法 `to_public_dict`。

- 类型：方法
- 定义位置：`src/miso/pupu/models.py:409`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `create(cls, *, display_name: str, source_kind: str, runtime: str, transport: str, normalized_config: dict[str, Any], required_secrets: list[str] | tuple[str, ...] | None=None, catalog_entry_id: str | None=None, status: str='ready_for_review', enabled: bool=False)`

`InstalledServer` 对外暴露的方法 `create`。

- 类型：方法
- 定义位置：`src/miso/pupu/models.py:431`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `from_record(cls, value: dict[str, Any])`

`InstalledServer` 对外暴露的方法 `from_record`。

- 类型：方法
- 定义位置：`src/miso/pupu/models.py:461`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

### 协作关系与关联类型

- `ToolPreview`
- `FieldSpec`
- `InstallProfile`
- `ConnectionIssue`
- `ConnectionTestResult`

### 最小调用示例

```python
InstalledServer(instance_id=..., instance_slug=..., display_name=..., source_kind=...)
```

### `src/miso/pupu/services.py`

Pupu 中负责目录刷新、导入、runtime 挂载与实例生命周期管理的服务层。

## GitHubRepositoryClient

用于Pupu 中负责目录刷新、导入、runtime 挂载与实例生命周期管理的服务层的实现类。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/miso/pupu/services.py:220` |
| 模块职责 | Pupu 中负责目录刷新、导入、runtime 挂载与实例生命周期管理的服务层。 |
| 继承/协议 | `-` |
| 导出状态 | 通过所属子包 `__init__` 导出。 |
| 对象类型 | 类；公开或包内可见。 |

### 构造表面

该类主要通过构造函数定义必需输入和校验逻辑。

- `__init__(self, *, http_client: httpx.Client | None=None)`

### 公共方法

#### `__init__(self, *, http_client: httpx.Client | None=None)`

初始化实例，并在类有约束时校验或强制转换构造参数。

- 类型：构造函数
- 定义位置：`src/miso/pupu/services.py:221`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `parse_repo_url(self, url: str)`

`GitHubRepositoryClient` 对外暴露的方法 `parse_repo_url`。

- 类型：方法
- 定义位置：`src/miso/pupu/services.py:224`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `get_default_branch(self, owner: str, repo: str)`

`GitHubRepositoryClient` 对外暴露的方法 `get_default_branch`。

- 类型：方法
- 定义位置：`src/miso/pupu/services.py:236`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `fetch_file(self, owner: str, repo: str, branch: str, path: str)`

`GitHubRepositoryClient` 对外暴露的方法 `fetch_file`。

- 类型：方法
- 定义位置：`src/miso/pupu/services.py:245`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

### 协作关系与关联类型

- `CatalogService`
- `ImportService`
- `NamespacedToolkit`
- `MCPTestService`
- `ManagedRuntimeHandle`

### 最小调用示例

```python
obj = GitHubRepositoryClient(...)
obj.parse_repo_url(...)
```

## CatalogService

用于Pupu 中负责目录刷新、导入、runtime 挂载与实例生命周期管理的服务层的实现类。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/miso/pupu/services.py:254` |
| 模块职责 | Pupu 中负责目录刷新、导入、runtime 挂载与实例生命周期管理的服务层。 |
| 继承/协议 | `-` |
| 导出状态 | 通过所属子包 `__init__` 导出。 |
| 对象类型 | 类；公开或包内可见。 |

### 构造表面

该类主要通过构造函数定义必需输入和校验逻辑。

- `__init__(self, *, remote_url: str | None=None, cache: FileCatalogCache | None=None, fetcher: Callable[[str, str | None], tuple[dict[str, Any] | None, str | None, bool]] | None=None, app_version: str | None=None, seed_entries: list[CatalogEntry] | None=None)`

### 公共方法

#### `__init__(self, *, remote_url: str | None=None, cache: FileCatalogCache | None=None, fetcher: Callable[[str, str | None], tuple[dict[str, Any] | None, str | None, bool]] | None=None, app_version: str | None=None, seed_entries: list[CatalogEntry] | None=None)`

初始化实例，并在类有约束时校验或强制转换构造参数。

- 类型：构造函数
- 定义位置：`src/miso/pupu/services.py:255`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `refresh(self, *, force: bool=False)`

`CatalogService` 对外暴露的方法 `refresh`。

- 类型：方法
- 定义位置：`src/miso/pupu/services.py:287`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `list_catalog(self, *, query: str | None=None, tags: list[str] | tuple[str, ...] | None=None, runtime: str | None=None)`

`CatalogService` 对外暴露的方法 `list_catalog`。

- 类型：方法
- 定义位置：`src/miso/pupu/services.py:334`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `get_entry(self, entry_id: str)`

`CatalogService` 对外暴露的方法 `get_entry`。

- 类型：方法
- 定义位置：`src/miso/pupu/services.py:368`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

### 协作关系与关联类型

- `GitHubRepositoryClient`
- `ImportService`
- `NamespacedToolkit`
- `MCPTestService`
- `ManagedRuntimeHandle`

### 最小调用示例

```python
obj = CatalogService(...)
obj.refresh(...)
```

## ImportService

用于Pupu 中负责目录刷新、导入、runtime 挂载与实例生命周期管理的服务层的实现类。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/miso/pupu/services.py:373` |
| 模块职责 | Pupu 中负责目录刷新、导入、runtime 挂载与实例生命周期管理的服务层。 |
| 继承/协议 | `-` |
| 导出状态 | 通过所属子包 `__init__` 导出。 |
| 对象类型 | 类；公开或包内可见。 |

### 构造表面

该类主要通过构造函数定义必需输入和校验逻辑。

- `__init__(self, *, github_client: GitHubRepositoryClient | None=None)`

### 公共方法

#### `__init__(self, *, github_client: GitHubRepositoryClient | None=None)`

初始化实例，并在类有约束时校验或强制转换构造参数。

- 类型：构造函数
- 定义位置：`src/miso/pupu/services.py:374`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `import_claude_config(self, *, json_text: str)`

`ImportService` 对外暴露的方法 `import_claude_config`。

- 类型：方法
- 定义位置：`src/miso/pupu/services.py:377`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `import_github_repo(self, *, url: str)`

`ImportService` 对外暴露的方法 `import_github_repo`。

- 类型：方法
- 定义位置：`src/miso/pupu/services.py:391`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `create_manual_draft(self, *, runtime: str, transport: str, name: str, config: dict[str, Any] | None=None)`

`ImportService` 对外暴露的方法 `create_manual_draft`。

- 类型：方法
- 定义位置：`src/miso/pupu/services.py:430`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

### 协作关系与关联类型

- `GitHubRepositoryClient`
- `CatalogService`
- `NamespacedToolkit`
- `MCPTestService`
- `ManagedRuntimeHandle`

### 最小调用示例

```python
obj = ImportService(...)
obj.import_claude_config(...)
```

## NamespacedToolkit

用于Pupu 中负责目录刷新、导入、runtime 挂载与实例生命周期管理的服务层的实现类。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/miso/pupu/services.py:646` |
| 模块职责 | Pupu 中负责目录刷新、导入、runtime 挂载与实例生命周期管理的服务层。 |
| 继承/协议 | `Toolkit` |
| 导出状态 | 通过所属子包 `__init__` 导出。 |
| 对象类型 | 类；公开或包内可见。 |

### 构造表面

该类主要通过构造函数定义必需输入和校验逻辑。

- `__init__(self, *, namespace: str, inner: Toolkit)`

### 公共方法

#### `__init__(self, *, namespace: str, inner: Toolkit)`

初始化实例，并在类有约束时校验或强制转换构造参数。

- 类型：构造函数
- 定义位置：`src/miso/pupu/services.py:647`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `refresh_tools(self)`

`NamespacedToolkit` 对外暴露的方法 `refresh_tools`。

- 类型：方法
- 定义位置：`src/miso/pupu/services.py:653`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `execute(self, function_name: str, arguments: dict[str, Any] | str | None)`

`NamespacedToolkit` 对外暴露的方法 `execute`。

- 类型：方法
- 定义位置：`src/miso/pupu/services.py:678`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

### 协作关系与关联类型

- `GitHubRepositoryClient`
- `CatalogService`
- `ImportService`
- `MCPTestService`
- `ManagedRuntimeHandle`

### 最小调用示例

```python
obj = NamespacedToolkit(...)
obj.refresh_tools(...)
```

## MCPTestService

用于Pupu 中负责目录刷新、导入、runtime 挂载与实例生命周期管理的服务层的实现类。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/miso/pupu/services.py:685` |
| 模块职责 | Pupu 中负责目录刷新、导入、runtime 挂载与实例生命周期管理的服务层。 |
| 继承/协议 | `-` |
| 导出状态 | 通过所属子包 `__init__` 导出。 |
| 对象类型 | 类；公开或包内可见。 |

### 构造表面

该类主要通过构造函数定义必需输入和校验逻辑。

- `__init__(self, *, secret_store: InMemorySecretStore, toolkit_factory: Callable[[InstalledServer, dict[str, Any]], Toolkit] | None=None)`

### 公共方法

#### `__init__(self, *, secret_store: InMemorySecretStore, toolkit_factory: Callable[[InstalledServer, dict[str, Any]], Toolkit] | None=None)`

初始化实例，并在类有约束时校验或强制转换构造参数。

- 类型：构造函数
- 定义位置：`src/miso/pupu/services.py:686`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `test(self, instance: InstalledServer)`

`MCPTestService` 对外暴露的方法 `test`。

- 类型：方法
- 定义位置：`src/miso/pupu/services.py:695`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

### 协作关系与关联类型

- `GitHubRepositoryClient`
- `CatalogService`
- `ImportService`
- `NamespacedToolkit`
- `ManagedRuntimeHandle`

### 最小调用示例

```python
obj = MCPTestService(...)
obj.test(...)
```

## ManagedRuntimeHandle

用于Pupu 中负责目录刷新、导入、runtime 挂载与实例生命周期管理的服务层的 dataclass 载荷。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/miso/pupu/services.py:885` |
| 模块职责 | Pupu 中负责目录刷新、导入、runtime 挂载与实例生命周期管理的服务层。 |
| 继承/协议 | `-` |
| 导出状态 | 未导出，应视为实现细节。 |
| 对象类型 | Dataclass；公开或包内可见。 |

### 字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `instance_id` | `str` | 构造时必需。 |
| `toolkit` | `Toolkit` | 构造时必需。 |
| `namespaced_toolkit` | `NamespacedToolkit` | 构造时必需。 |
| `cached_tools` | `tuple[ToolPreview, ...]` | 构造时必需。 |

### 公共方法

该类型除了 dataclass/protocol 结构外不暴露公共方法。

### 协作关系与关联类型

- `GitHubRepositoryClient`
- `CatalogService`
- `ImportService`
- `NamespacedToolkit`
- `MCPTestService`

### 最小调用示例

```python
ManagedRuntimeHandle(instance_id=..., toolkit=..., namespaced_toolkit=..., cached_tools=...)
```

## MCPRuntimeManager

用于Pupu 中负责目录刷新、导入、runtime 挂载与实例生命周期管理的服务层的实现类。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/miso/pupu/services.py:892` |
| 模块职责 | Pupu 中负责目录刷新、导入、runtime 挂载与实例生命周期管理的服务层。 |
| 继承/协议 | `-` |
| 导出状态 | 通过所属子包 `__init__` 导出。 |
| 对象类型 | 类；公开或包内可见。 |

### 构造表面

该类主要通过构造函数定义必需输入和校验逻辑。

- `__init__(self, *, secret_store: InMemorySecretStore, toolkit_factory: Callable[[InstalledServer, dict[str, Any]], Toolkit] | None=None)`

### 公共方法

#### `__init__(self, *, secret_store: InMemorySecretStore, toolkit_factory: Callable[[InstalledServer, dict[str, Any]], Toolkit] | None=None)`

初始化实例，并在类有约束时校验或强制转换构造参数。

- 类型：构造函数
- 定义位置：`src/miso/pupu/services.py:893`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `ensure_enabled(self, instance: InstalledServer)`

`MCPRuntimeManager` 对外暴露的方法 `ensure_enabled`。

- 类型：方法
- 定义位置：`src/miso/pupu/services.py:904`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `disable(self, instance_id: str)`

`MCPRuntimeManager` 对外暴露的方法 `disable`。

- 类型：方法
- 定义位置：`src/miso/pupu/services.py:925`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `attach(self, chat_id: str, instances: list[InstalledServer])`

`MCPRuntimeManager` 对外暴露的方法 `attach`。

- 类型：方法
- 定义位置：`src/miso/pupu/services.py:936`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `toolkits_for_chat(self, chat_id: str)`

`MCPRuntimeManager` 对外暴露的方法 `toolkits_for_chat`。

- 类型：方法
- 定义位置：`src/miso/pupu/services.py:945`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

### 协作关系与关联类型

- `GitHubRepositoryClient`
- `CatalogService`
- `ImportService`
- `NamespacedToolkit`
- `MCPTestService`

### 最小调用示例

```python
obj = MCPRuntimeManager(...)
obj.ensure_enabled(...)
```

## PupuMCPService

Pupu 子系统门面，向外暴露目录、导入、持久化、测试、启停和 chat 挂载操作。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/miso/pupu/services.py:954` |
| 模块职责 | Pupu 中负责目录刷新、导入、runtime 挂载与实例生命周期管理的服务层。 |
| 继承/协议 | `-` |
| 导出状态 | 通过所属子包 `__init__` 导出。 |
| 对象类型 | 类；公开或包内可见。 |

### 构造表面

该类主要通过构造函数定义必需输入和校验逻辑。

- `__init__(self, *, catalog_service: CatalogService | None=None, import_service: ImportService | None=None, installed_server_store: FileInstalledServerStore | None=None, secret_store: InMemorySecretStore | None=None, test_service: MCPTestService | None=None, runtime_manager: MCPRuntimeManager | None=None)`

### 公共方法

#### `__init__(self, *, catalog_service: CatalogService | None=None, import_service: ImportService | None=None, installed_server_store: FileInstalledServerStore | None=None, secret_store: InMemorySecretStore | None=None, test_service: MCPTestService | None=None, runtime_manager: MCPRuntimeManager | None=None)`

初始化实例，并在类有约束时校验或强制转换构造参数。

- 类型：构造函数
- 定义位置：`src/miso/pupu/services.py:955`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `list_catalog(self, *, query: str | None=None, tags: list[str] | tuple[str, ...] | None=None, runtime: str | None=None)`

`PupuMCPService` 对外暴露的方法 `list_catalog`。

- 类型：方法
- 定义位置：`src/miso/pupu/services.py:973`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `import_claude_config(self, *, json_text: str)`

`PupuMCPService` 对外暴露的方法 `import_claude_config`。

- 类型：方法
- 定义位置：`src/miso/pupu/services.py:983`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `import_github_repo(self, *, url: str)`

`PupuMCPService` 对外暴露的方法 `import_github_repo`。

- 类型：方法
- 定义位置：`src/miso/pupu/services.py:988`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `create_manual_draft(self, *, runtime: str, transport: str, name: str, config: dict[str, Any] | None=None)`

`PupuMCPService` 对外暴露的方法 `create_manual_draft`。

- 类型：方法
- 定义位置：`src/miso/pupu/services.py:993`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `save_installed_server(self, *, draft_entry_id: str, profile_id: str, display_name: str | None=None, config: dict[str, Any] | None=None, secrets: dict[str, str] | None=None)`

`PupuMCPService` 对外暴露的方法 `save_installed_server`。

- 类型：方法
- 定义位置：`src/miso/pupu/services.py:1010`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `list_installed_servers(self)`

`PupuMCPService` 对外暴露的方法 `list_installed_servers`。

- 类型：方法
- 定义位置：`src/miso/pupu/services.py:1076`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `get_installed_server_detail(self, *, instance_id: str)`

`PupuMCPService` 对外暴露的方法 `get_installed_server_detail`。

- 类型：方法
- 定义位置：`src/miso/pupu/services.py:1082`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `test_installed_server(self, *, instance_id: str)`

`PupuMCPService` 对外暴露的方法 `test_installed_server`。

- 类型：方法
- 定义位置：`src/miso/pupu/services.py:1086`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `enable_installed_server(self, *, instance_id: str)`

`PupuMCPService` 对外暴露的方法 `enable_installed_server`。

- 类型：方法
- 定义位置：`src/miso/pupu/services.py:1120`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `disable_installed_server(self, *, instance_id: str)`

`PupuMCPService` 对外暴露的方法 `disable_installed_server`。

- 类型：方法
- 定义位置：`src/miso/pupu/services.py:1143`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `attach_servers_to_chat(self, *, chat_id: str, instance_ids: list[str])`

`PupuMCPService` 对外暴露的方法 `attach_servers_to_chat`。

- 类型：方法
- 定义位置：`src/miso/pupu/services.py:1151`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `get_chat_toolkits(self, chat_id: str)`

`PupuMCPService` 对外暴露的方法 `get_chat_toolkits`。

- 类型：方法
- 定义位置：`src/miso/pupu/services.py:1161`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `listCatalog(self, filters: dict[str, Any] | None=None)`

`PupuMCPService` 对外暴露的方法 `listCatalog`。

- 类型：方法
- 定义位置：`src/miso/pupu/services.py:1226`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `importClaudeConfig(self, payload: dict[str, Any])`

`PupuMCPService` 对外暴露的方法 `importClaudeConfig`。

- 类型：方法
- 定义位置：`src/miso/pupu/services.py:1234`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `importGitHubRepo(self, payload: dict[str, Any])`

`PupuMCPService` 对外暴露的方法 `importGitHubRepo`。

- 类型：方法
- 定义位置：`src/miso/pupu/services.py:1237`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `createManualDraft(self, payload: dict[str, Any])`

`PupuMCPService` 对外暴露的方法 `createManualDraft`。

- 类型：方法
- 定义位置：`src/miso/pupu/services.py:1240`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `saveInstalledServer(self, payload: dict[str, Any])`

`PupuMCPService` 对外暴露的方法 `saveInstalledServer`。

- 类型：方法
- 定义位置：`src/miso/pupu/services.py:1248`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `listInstalledServers(self)`

`PupuMCPService` 对外暴露的方法 `listInstalledServers`。

- 类型：方法
- 定义位置：`src/miso/pupu/services.py:1257`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `getInstalledServerDetail(self, payload: dict[str, Any])`

`PupuMCPService` 对外暴露的方法 `getInstalledServerDetail`。

- 类型：方法
- 定义位置：`src/miso/pupu/services.py:1260`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `testInstalledServer(self, payload: dict[str, Any])`

`PupuMCPService` 对外暴露的方法 `testInstalledServer`。

- 类型：方法
- 定义位置：`src/miso/pupu/services.py:1263`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `enableInstalledServer(self, payload: dict[str, Any])`

`PupuMCPService` 对外暴露的方法 `enableInstalledServer`。

- 类型：方法
- 定义位置：`src/miso/pupu/services.py:1266`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `disableInstalledServer(self, payload: dict[str, Any])`

`PupuMCPService` 对外暴露的方法 `disableInstalledServer`。

- 类型：方法
- 定义位置：`src/miso/pupu/services.py:1269`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `attachServersToChat(self, payload: dict[str, Any])`

`PupuMCPService` 对外暴露的方法 `attachServersToChat`。

- 类型：方法
- 定义位置：`src/miso/pupu/services.py:1272`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

### 生命周期与运行时角色

- 构造时串联 catalog service、import service、installed-server store、secret store、test service 与 runtime manager。
- 目录和导入方法只负责列出条目或生成草稿，不会直接修改 runtime 挂载。
- installed-server 方法负责持久化草稿选择、校验配置/密钥，并把启停与 chat 挂载委托给 runtime manager。
- CamelCase 别名保留为兼容层，对应到底层 snake_case 服务表面。

### 协作关系与关联类型

- `GitHubRepositoryClient`
- `CatalogService`
- `ImportService`
- `NamespacedToolkit`
- `MCPTestService`

### 最小调用示例

```python
obj = PupuMCPService(...)
obj.list_catalog(...)
```

### `src/miso/pupu/stores.py`

Pupu 使用的持久化与 secret-store 辅助对象。

## FileCatalogCache

用于Pupu 使用的持久化与 secret-store 辅助对象的实现类。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/miso/pupu/stores.py:19` |
| 模块职责 | Pupu 使用的持久化与 secret-store 辅助对象。 |
| 继承/协议 | `-` |
| 导出状态 | 通过所属子包 `__init__` 导出。 |
| 对象类型 | 类；公开或包内可见。 |

### 构造表面

该类主要通过构造函数定义必需输入和校验逻辑。

- `__init__(self, path: str | Path | None=None)`

### 公共方法

#### `__init__(self, path: str | Path | None=None)`

初始化实例，并在类有约束时校验或强制转换构造参数。

- 类型：构造函数
- 定义位置：`src/miso/pupu/stores.py:20`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `load(self)`

`FileCatalogCache` 对外暴露的方法 `load`。

- 类型：方法
- 定义位置：`src/miso/pupu/stores.py:23`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `save(self, *, etag: str | None, payload: dict[str, Any])`

`FileCatalogCache` 对外暴露的方法 `save`。

- 类型：方法
- 定义位置：`src/miso/pupu/stores.py:28`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

### 协作关系与关联类型

- `FileInstalledServerStore`
- `InMemorySecretStore`

### 最小调用示例

```python
obj = FileCatalogCache(...)
obj.load(...)
```

## FileInstalledServerStore

用于Pupu 使用的持久化与 secret-store 辅助对象的实现类。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/miso/pupu/stores.py:36` |
| 模块职责 | Pupu 使用的持久化与 secret-store 辅助对象。 |
| 继承/协议 | `-` |
| 导出状态 | 通过所属子包 `__init__` 导出。 |
| 对象类型 | 类；公开或包内可见。 |

### 构造表面

该类主要通过构造函数定义必需输入和校验逻辑。

- `__init__(self, path: str | Path | None=None)`

### 公共方法

#### `__init__(self, path: str | Path | None=None)`

初始化实例，并在类有约束时校验或强制转换构造参数。

- 类型：构造函数
- 定义位置：`src/miso/pupu/stores.py:37`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `list_instances(self)`

`FileInstalledServerStore` 对外暴露的方法 `list_instances`。

- 类型：方法
- 定义位置：`src/miso/pupu/stores.py:40`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `get_instance(self, instance_id: str)`

`FileInstalledServerStore` 对外暴露的方法 `get_instance`。

- 类型：方法
- 定义位置：`src/miso/pupu/stores.py:50`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `save_instance(self, instance: InstalledServer)`

`FileInstalledServerStore` 对外暴露的方法 `save_instance`。

- 类型：方法
- 定义位置：`src/miso/pupu/stores.py:56`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

### 协作关系与关联类型

- `FileCatalogCache`
- `InMemorySecretStore`

### 最小调用示例

```python
obj = FileInstalledServerStore(...)
obj.list_instances(...)
```

## InMemorySecretStore

用于Pupu 使用的持久化与 secret-store 辅助对象的实现类。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/miso/pupu/stores.py:79` |
| 模块职责 | Pupu 使用的持久化与 secret-store 辅助对象。 |
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
- 定义位置：`src/miso/pupu/stores.py:80`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `set_secret(self, instance_id: str, target: str, value: str)`

`InMemorySecretStore` 对外暴露的方法 `set_secret`。

- 类型：方法
- 定义位置：`src/miso/pupu/stores.py:83`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `has_secret(self, instance_id: str, target: str)`

`InMemorySecretStore` 对外暴露的方法 `has_secret`。

- 类型：方法
- 定义位置：`src/miso/pupu/stores.py:86`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `clear_secret(self, instance_id: str, target: str)`

`InMemorySecretStore` 对外暴露的方法 `clear_secret`。

- 类型：方法
- 定义位置：`src/miso/pupu/stores.py:89`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `resolve_secrets(self, instance_id: str, targets: list[str] | tuple[str, ...] | None=None)`

`InMemorySecretStore` 对外暴露的方法 `resolve_secrets`。

- 类型：方法
- 定义位置：`src/miso/pupu/stores.py:92`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

### 协作关系与关联类型

- `FileCatalogCache`
- `FileInstalledServerStore`

### 最小调用示例

```python
obj = InMemorySecretStore(...)
obj.set_secret(...)
```
