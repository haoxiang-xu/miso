# Toolkit 实现参考

覆盖内置 toolkit、MCP bridge、terminal runtime 内部对象以及工作区安全基类。

| 指标 | 值 |
| --- | --- |
| 类数量 | 8 |
| Dataclass | 1 |
| 协议 | 0 |
| 仅内部类型 | 2 |

## 覆盖地图

| 类 | 源码 | 导出 | 类型 |
| --- | --- | --- | --- |
| `BuiltinToolkit` | `src/unchain/toolkits/base.py:10` | subpackage | class |
| `AskUserToolkit` | `src/unchain/toolkits/builtin/ask_user/ask_user.py:7` | subpackage | class |
| `ExternalAPIToolkit` | `src/unchain/toolkits/builtin/external_api/external_api.py:12` | subpackage | class |
| `TerminalToolkit` | `src/unchain/toolkits/builtin/terminal/terminal.py:10` | subpackage | class |
| `_TerminalSession` | `src/unchain/toolkits/builtin/terminal_runtime.py:15` | internal | dataclass |
| `_TerminalRuntime` | `src/unchain/toolkits/builtin/terminal_runtime.py:22` | internal | class |
| `WorkspaceToolkit` | `src/unchain/toolkits/builtin/workspace/workspace.py:24` | subpackage | class |
| `MCPToolkit` | `src/unchain/toolkits/mcp.py:62` | subpackage | class |

### `src/unchain/toolkits/base.py`

内置 toolkit 共享的 workspace 感知基类。

## BuiltinToolkit

具备 workspace root 和 pin execution context 能力的内置 toolkit 基类。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/unchain/toolkits/base.py:10` |
| 模块职责 | 内置 toolkit 共享的 workspace 感知基类。 |
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
- `ExternalAPIToolkit`
- `TerminalToolkit`
- `_TerminalSession`
- `_TerminalRuntime`

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
- `ExternalAPIToolkit`
- `TerminalToolkit`
- `_TerminalSession`
- `_TerminalRuntime`

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
- `TerminalToolkit`
- `_TerminalSession`
- `_TerminalRuntime`

### 最小调用示例

```python
obj = ExternalAPIToolkit(...)
obj.http_get(...)
```

### `src/unchain/toolkits/builtin/terminal/terminal.py`

面向用户的 terminal toolkit，底层依赖内部 terminal runtime。

## TerminalToolkit

用于面向用户的 terminal toolkit，底层依赖内部 terminal runtime的实现类。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/unchain/toolkits/builtin/terminal/terminal.py:10` |
| 模块职责 | 面向用户的 terminal toolkit，底层依赖内部 terminal runtime。 |
| 继承/协议 | `BuiltinToolkit` |
| 导出状态 | 通过所属子包 `__init__` 导出。 |
| 对象类型 | 类；公开或包内可见。 |

### 构造表面

该类主要通过构造函数定义必需输入和校验逻辑。

- `__init__(self, *, workspace_root: str | Path | None=None, terminal_strict_mode: bool=True)`

### 公共方法

#### `__init__(self, *, workspace_root: str | Path | None=None, terminal_strict_mode: bool=True)`

初始化实例，并在类有约束时校验或强制转换构造参数。

- 类型：构造函数
- 定义位置：`src/unchain/toolkits/builtin/terminal/terminal.py:13`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `terminal_exec(self, command: str, cwd: str='.', timeout_seconds: int=30, max_output_chars: int=20000, shell: str='/bin/bash')`

通过允许的 shell 执行真实命令字符串，保留管道、重定向、引用与 `&&` 等正常 shell 语义。

- 类型：方法
- 定义位置：`src/unchain/toolkits/builtin/terminal/terminal.py:34`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `terminal_session_open(self, shell: str='/bin/bash', cwd: str='.', timeout_seconds: int=3600)`

Open a persistent shell session and return a session id.

- 类型：方法
- 定义位置：`src/unchain/toolkits/builtin/terminal/terminal.py:49`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `terminal_session_write(self, session_id: str, input: str='', yield_time_ms: int=300, max_output_chars: int=20000)`

Write to a session stdin and collect available output.

- 类型：方法
- 定义位置：`src/unchain/toolkits/builtin/terminal/terminal.py:62`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `terminal_session_close(self, session_id: str)`

Close a persistent shell session and return final output.

- 类型：方法
- 定义位置：`src/unchain/toolkits/builtin/terminal/terminal.py:77`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `shutdown(self)`

`TerminalToolkit` 对外暴露的方法 `shutdown`。

- 类型：方法
- 定义位置：`src/unchain/toolkits/builtin/terminal/terminal.py:81`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

### 协作关系与关联类型

- `BuiltinToolkit`
- `AskUserToolkit`
- `ExternalAPIToolkit`
- `_TerminalSession`
- `_TerminalRuntime`

### 最小调用示例

```python
obj = TerminalToolkit(...)
obj.terminal_exec(...)
```

### `src/unchain/toolkits/builtin/terminal_runtime.py`

TerminalToolkit 使用的内部 session/runtime 实现。

## _TerminalSession

用于TerminalToolkit 使用的内部 session/runtime 实现的 dataclass 载荷。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/unchain/toolkits/builtin/terminal_runtime.py:15` |
| 模块职责 | TerminalToolkit 使用的内部 session/runtime 实现。 |
| 继承/协议 | `-` |
| 导出状态 | 未导出，应视为实现细节。 |
| 对象类型 | Dataclass；内部实现。 |

### 内部实现说明

Owned by `_TerminalRuntime` session bookkeeping.

### 字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `process` | `subprocess.Popen[bytes]` | 构造时必需。 |
| `cwd` | `Path` | 构造时必需。 |
| `opened_at` | `float` | 构造时必需。 |
| `timeout_seconds` | `int` | 构造时必需。 |

### 公共方法

该类型除了 dataclass/protocol 结构外不暴露公共方法。

### 协作关系与关联类型

- `_TerminalRuntime`

### 最小调用示例

```python
_TerminalSession(process=..., cwd=..., opened_at=..., timeout_seconds=...)
```

## _TerminalRuntime

供TerminalToolkit 使用的内部 session/runtime 实现使用的内部辅助对象，不应被视为稳定外部接口。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/unchain/toolkits/builtin/terminal_runtime.py:22` |
| 模块职责 | TerminalToolkit 使用的内部 session/runtime 实现。 |
| 继承/协议 | `-` |
| 导出状态 | 未导出，应视为实现细节。 |
| 对象类型 | 类；内部实现。 |

### 内部实现说明

Owned by `TerminalToolkit` as the stateful terminal backend.

### 构造表面

该类主要通过构造函数定义必需输入和校验逻辑。

- `__init__(self, workspace_root: Path, strict_mode: bool=True)`

### 公共方法

#### `__init__(self, workspace_root: Path, strict_mode: bool=True)`

初始化实例，并在类有约束时校验或强制转换构造参数。

- 类型：构造函数
- 定义位置：`src/unchain/toolkits/builtin/terminal_runtime.py:45`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `execute(self, command: str, cwd: str='.', timeout_seconds: int=30, max_output_chars: int=20000)`

`_TerminalRuntime` 对外暴露的方法 `execute`。

- 类型：方法
- 定义位置：`src/unchain/toolkits/builtin/terminal_runtime.py:158`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `open_session(self, shell: str='/bin/bash', cwd: str='.', timeout_seconds: int=3600)`

`_TerminalRuntime` 对外暴露的方法 `open_session`。

- 类型：方法
- 定义位置：`src/unchain/toolkits/builtin/terminal_runtime.py:275`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `write_session(self, session_id: str, input: str='', yield_time_ms: int=300, max_output_chars: int=20000)`

`_TerminalRuntime` 对外暴露的方法 `write_session`。

- 类型：方法
- 定义位置：`src/unchain/toolkits/builtin/terminal_runtime.py:349`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `close_session(self, session_id: str)`

`_TerminalRuntime` 对外暴露的方法 `close_session`。

- 类型：方法
- 定义位置：`src/unchain/toolkits/builtin/terminal_runtime.py:415`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `close_all_sessions(self)`

`_TerminalRuntime` 对外暴露的方法 `close_all_sessions`。

- 类型：方法
- 定义位置：`src/unchain/toolkits/builtin/terminal_runtime.py:446`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

### 协作关系与关联类型

- `_TerminalSession`

### 最小调用示例

```python
# See `src/unchain/toolkits/builtin/terminal_runtime.py` for the owning call flow.

```

### `src/unchain/toolkits/builtin/workspace/workspace.py`

工作区文件、行编辑、搜索、目录、AST 与 pin 管理 toolkit。

## WorkspaceToolkit

内置 workspace toolkit，覆盖文件 IO、行编辑、搜索、目录枚举、AST 读取和 pin context 管理。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/unchain/toolkits/builtin/workspace/workspace.py:24` |
| 模块职责 | 工作区文件、行编辑、搜索、目录、AST 与 pin 管理 toolkit。 |
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
- 定义位置：`src/unchain/toolkits/builtin/workspace/workspace.py:27`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `read_files(self, paths: list[str], max_chars_per_file: int=20000, max_total_chars: int=50000, ast_threshold: int=256)`

Read multiple UTF-8 text files from workspace.

- 类型：方法
- 定义位置：`src/unchain/toolkits/builtin/workspace/workspace.py:377`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。
- 补充：:param paths: File paths relative or absolute inside workspace.
:param max_chars_per_file: Truncate each file after this many characters.
:param max_total_chars: Stop once the combined returned content reaches this many characters.
:param ast_threshold: 大于零时，大文件源码在支持的语言上可自动返回 AST 而不是纯文本。

#### `write_file(self, path: str, content: str, append: bool=False)`

Write UTF-8 text file into workspace (overwrite or append).

- 类型：方法
- 定义位置：`src/unchain/toolkits/builtin/workspace/workspace.py:464`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。
- 补充：:param path: Relative or absolute path inside workspace.
:param content: Text content to write.
:param append: If True, append to existing content instead of overwriting.

#### `list_directories(self, paths: list[str], recursive: bool=False, max_entries_per_directory: int=200, max_total_entries: int=500)`

List multiple workspace directories in one call.

- 类型：方法
- 定义位置：`src/unchain/toolkits/builtin/workspace/workspace.py:564`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。
- 补充：:param paths: Directory paths relative or absolute inside workspace.
:param recursive: If True, list descendants recursively for each directory.
:param max_entries_per_directory: Maximum entries to return per directory.
:param max_total_entries: Stop once the combined returned entries reach this many items.

#### `search_text(self, pattern: str, path: str='.', max_results: int=100, case_sensitive: bool=False, file_glob: str | None=None, context_lines: int=0)`

Search text pattern across workspace files.

- 类型：方法
- 定义位置：`src/unchain/toolkits/builtin/workspace/workspace.py:642`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。
- 补充：:param pattern: Regex pattern to search for.
:param path: Directory or file path to search within.
:param max_results: Maximum number of matches to return.
:param case_sensitive: Whether the search is case-sensitive.
:param file_glob: Optional filename filter applied while scanning files.
:param context_lines: Optional number of surrounding lines to include with each match.

#### `read_lines(self, path: str, start: int=1, end: int | None=None)`

Read a range of lines from a file (1-based, inclusive).

- 类型：方法
- 定义位置：`src/unchain/toolkits/builtin/workspace/workspace.py:799`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。
- 补充：:param path: File path relative to workspace root.
:param start: First line number to read (1-based).
:param end: Last line number to read (inclusive). Defaults to end of file.

#### `insert_lines(self, path: str, line: int, content: str)`

Insert text before a given line number (1-based).

- 类型：方法
- 定义位置：`src/unchain/toolkits/builtin/workspace/workspace.py:833`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。
- 补充：:param path: File path relative to workspace root.
:param line: Line number to insert before (1-based). Use total_lines+1 to append.
:param content: Text content to insert (will be split into lines).

#### `replace_lines(self, path: str, start: int, end: int, content: str)`

Replace a range of lines [start, end] (1-based, inclusive) with new content.

- 类型：方法
- 定义位置：`src/unchain/toolkits/builtin/workspace/workspace.py:865`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。
- 补充：:param path: File path relative to workspace root.
:param start: First line to replace (1-based).
:param end: Last line to replace (inclusive).
:param content: Replacement text (can be any number of lines).

#### `delete_lines(self, path: str, start: int, end: int)`

Delete a range of lines [start, end] (1-based, inclusive).

- 类型：方法
- 定义位置：`src/unchain/toolkits/builtin/workspace/workspace.py:899`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。
- 补充：:param path: File path relative to workspace root.
:param start: First line to delete (1-based).
:param end: Last line to delete (inclusive).

#### `pin_file_context(self, path: str, start: int | None=None, end: int | None=None, start_with: str | None=None, end_with: str | None=None, reason: str | None=None)`

Pin a file or line range into the current session.

- 类型：方法
- 定义位置：`src/unchain/toolkits/builtin/workspace/workspace.py:1064`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

#### `unpin_file_context(self, pin_id: str | None=None, path: str | None=None, start: int | None=None, end: int | None=None, all: bool=False)`

Remove one or more pinned file contexts from the current session.

- 类型：方法
- 定义位置：`src/unchain/toolkits/builtin/workspace/workspace.py:1155`
- 返回形状：以源码签名和方法体为准；多数面对调用方的表面会返回 dict 载荷，或返回序列化后的 dataclass 内容。
- 错误与校验：该表面可能把无效输入导致的 `ValueError`/`TypeError` 继续向上传播；工具式方法也可能返回 `{"error": ...}` 载荷。

### 生命周期与运行时角色

- 构造时会围绕 workspace root 注册一组精简的结构化文件、目录、行编辑、搜索、AST 自动升级读取与 pin 管理工具。
- 每个 workspace 操作都通过基类的安全路径解析，确保动作不会逃逸出 workspace root。
- pin 相关操作通过 runtime 注入的 execution context 读写 session 级 pin 状态。
- 从调用者视角看，这个类本身近似无状态；有状态部分主要在会话存储和文件系统里。

### 协作关系与关联类型

- `BuiltinToolkit`
- `AskUserToolkit`
- `ExternalAPIToolkit`
- `TerminalToolkit`
- `_TerminalSession`

### 最小调用示例

```python
obj = WorkspaceToolkit(...)
obj.read_files(...)
```

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
- `ExternalAPIToolkit`
- `TerminalToolkit`
- `_TerminalSession`

### 最小调用示例

```python
obj = MCPToolkit(...)
obj.connect(...)
```
