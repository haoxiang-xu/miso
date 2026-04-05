# Toolkit 实现参考

覆盖内置 toolkit、MCP bridge 以及基类。

| 指标 | 值 |
| --- | --- |
| 类数量 | 5 |
| Dataclass | 0 |
| 协议 | 0 |
| 仅内部类型 | 0 |

## 覆盖地图

| 类 | 源码 | 导出 | 类型 |
| --- | --- | --- | --- |
| `BuiltinToolkit` | `src/unchain/toolkits/base.py:10` | subpackage | class |
| `AskUserToolkit` | `src/unchain/toolkits/builtin/ask_user/ask_user.py:7` | subpackage | class |
| `CodeToolkit` | `src/unchain/toolkits/builtin/code/code.py:30` | subpackage | class |
| `ExternalAPIToolkit` | `src/unchain/toolkits/builtin/external_api/external_api.py:12` | subpackage | class |
| `MCPToolkit` | `src/unchain/toolkits/mcp.py:62` | subpackage | class |

### `src/unchain/toolkits/base.py`

内置 toolkit 共享的基类。

## BuiltinToolkit

内置 toolkit 基类。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/unchain/toolkits/base.py:10` |
| 模块职责 | 内置 toolkit 共享的基类。 |
| 继承/协议 | `Toolkit` |
| 导出状态 | 通过所属子包 `__init__` 导出。 |
| 对象类型 | 类；公开或包内可见。 |

### 构造表面

该类主要通过构造函数定义必需输入和校验逻辑。

- `__init__(self, *, workspace_root: str | Path | None=None)`

### 属性

- `@property current_execution_context`: 公开属性访问器。

### 公共方法

#### `__init__(self, *, workspace_root: str | Path | None=None)`

初始化实例，并在类有约束时校验或强制转换构造参数。

- 类型：构造函数
- 定义位置：`src/unchain/toolkits/base.py:23`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `push_execution_context(self, context: WorkspacePinExecutionContext)`

`BuiltinToolkit` 对外暴露的方法 `push_execution_context`。

- 类型：方法
- 定义位置：`src/unchain/toolkits/base.py:45`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `pop_execution_context(self)`

`BuiltinToolkit` 对外暴露的方法 `pop_execution_context`。

- 类型：方法
- 定义位置：`src/unchain/toolkits/base.py:48`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

### 协作关系与关联类型

- `AskUserToolkit`
- `CodeToolkit`
- `ExternalAPIToolkit`

### 最小调用示例

```python
obj = BuiltinToolkit(...)
obj.push_execution_context(...)
```

### `src/unchain/toolkits/builtin/ask_user/ask_user.py`

承载 ask-user 保留工具的内置 toolkit。

## AskUserToolkit

用于承载 ask-user 保留工具的内置 toolkit的实现类。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/unchain/toolkits/builtin/ask_user/ask_user.py:7` |
| 模块职责 | 承载 ask-user 保留工具的内置 toolkit。 |
| 继承/协议 | `Toolkit` |
| 导出状态 | 通过所属子包 `__init__` 导出。 |
| 对象类型 | 类；公开或包内可见。 |

### 构造表面

该类主要通过构造函数定义必需输入和校验逻辑。

- `__init__(self)`

### 公共方法

#### `__init__(self)`

初始化实例，并在类有约束时校验或强制转换构造参数。

- 类型：构造函数
- 定义位置：`src/unchain/toolkits/builtin/ask_user/ask_user.py:10`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `ask_user_question(self, **kwargs)`

Reserved runtime placeholder for structured user input requests.

- 类型：方法
- 定义位置：`src/unchain/toolkits/builtin/ask_user/ask_user.py:20`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

### 协作关系与关联类型

- `BuiltinToolkit`
- `CodeToolkit`
- `ExternalAPIToolkit`

### 最小调用示例

```python
obj = AskUserToolkit(...)
obj.ask_user_question(...)
```

### `src/unchain/toolkits/builtin/external_api/external_api.py`

提供简单 GET/POST HTTP 能力的外部 API toolkit。

## ExternalAPIToolkit

用于提供简单 GET/POST HTTP 能力的外部 API toolkit的实现类。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/unchain/toolkits/builtin/external_api/external_api.py:12` |
| 模块职责 | 提供简单 GET/POST HTTP 能力的外部 API toolkit。 |
| 继承/协议 | `BuiltinToolkit` |
| 导出状态 | 通过所属子包 `__init__` 导出。 |
| 对象类型 | 类；公开或包内可见。 |

### 构造表面

该类主要通过构造函数定义必需输入和校验逻辑。

- `__init__(self, *, workspace_root: str | Path | None=None)`

### 公共方法

#### `__init__(self, *, workspace_root: str | Path | None=None)`

初始化实例，并在类有约束时校验或强制转换构造参数。

- 类型：构造函数
- 定义位置：`src/unchain/toolkits/builtin/external_api/external_api.py:15`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `http_get(self, url: str, headers: dict[str, str] | None=None, timeout_seconds: int=30, max_response_chars: int=50000)`

Send a GET request to an external API endpoint.

- 类型：方法
- 定义位置：`src/unchain/toolkits/builtin/external_api/external_api.py:29`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。
- 补充：:param url: Full URL to send the GET request to.
:param headers: Optional dictionary of HTTP headers to include.
:param timeout_seconds: Maximum seconds to wait for response.
:param max_response_chars: Maximum response body chars to return.

#### `http_post(self, url: str, body: str | dict[str, Any], headers: dict[str, str] | None=None, timeout_seconds: int=30, max_response_chars: int=50000)`

Send a POST request to an external API endpoint.

- 类型：方法
- 定义位置：`src/unchain/toolkits/builtin/external_api/external_api.py:90`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。
- 补充：:param url: Full URL to send the POST request to.
:param body: Request body as string or dict (dict will be JSON-encoded).
:param headers: Optional dictionary of HTTP headers to include.
:param timeout_seconds: Maximum seconds to wait for response.
:param max_response_chars: Maximum response body chars to return.

### 协作关系与关联类型

- `BuiltinToolkit`
- `AskUserToolkit`
- `CodeToolkit`

### 最小调用示例

```python
obj = ExternalAPIToolkit(...)
obj.http_get(...)
```

### `src/unchain/toolkits/mcp.py`

### `src/unchain/toolkits/mcp.py`

把远端 MCP server 工具桥接为本地 Toolkit 的实现。

## MCPToolkit

把 MCP server 工具桥接到本地 runtime 的 toolkit。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/unchain/toolkits/mcp.py:62` |
| 模块职责 | 把远端 MCP server 工具桥接为本地 Toolkit 的实现。 |
| 继承/协议 | `Toolkit` |
| 导出状态 | 通过所属子包 `__init__` 导出。 |
| 对象类型 | 类；公开或包内可见。 |

### 构造表面

该类主要通过构造函数定义必需输入和校验逻辑。

- `__init__(self, *, command: str | None=None, args: list[str] | None=None, env: dict[str, str] | None=None, cwd: str | None=None, url: str | None=None, headers: dict[str, str] | None=None, transport: str | None=None)`

### 属性

- `@property connected`: 公开属性访问器。

### 公共方法

#### `__init__(self, *, command: str | None=None, args: list[str] | None=None, env: dict[str, str] | None=None, cwd: str | None=None, url: str | None=None, headers: dict[str, str] | None=None, transport: str | None=None)`

初始化实例，并在类有约束时校验或强制转换构造参数。

- 类型：构造函数
- 定义位置：`src/unchain/toolkits/mcp.py:83`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `connect(self)`

Connect to the MCP server, discover tools, and populate the toolkit.

- 类型：方法
- 定义位置：`src/unchain/toolkits/mcp.py:127`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。
- 补充：This method blocks until the session is ready and tools have been
fetched.  It is safe to call ``connect()`` on an already-connected
instance (it will be a no-op).

Returns ``self`` for convenient chaining.

#### `disconnect(self)`

Disconnect from the MCP server and clean up resources.

- 类型：方法
- 定义位置：`src/unchain/toolkits/mcp.py:156`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `execute(self, function_name: str, arguments: dict[str, Any] | str | None)`

Execute a tool on the MCP server.

- 类型：方法
- 定义位置：`src/unchain/toolkits/mcp.py:185`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。
- 补充：Falls back to local toolkit execution if the server is disconnected.

### 协作关系与关联类型

- `BuiltinToolkit`
- `AskUserToolkit`
- `CodeToolkit`
- `ExternalAPIToolkit`

### 最小调用示例

```python
obj = MCPToolkit(...)
obj.connect(...)
```
