# Toolkit 实现参考

覆盖内置 toolkit、MCP bridge 以及基类。

| 指标 | 值 |
| --- | --- |
| 类数量 | 6 |
| Dataclass | 2 |
| 协议 | 0 |
| 仅内部类型 | 2 |

## 覆盖地图

| 类 | 源码 | 导出 | 类型 |
| --- | --- | --- | --- |
| `BuiltinToolkit` | `src/unchain/toolkits/base.py:10` | subpackage | class |
| `CoreToolkit` | `src/unchain/toolkits/builtin/core/core.py:30` | subpackage | class |
| `ExternalAPIToolkit` | `src/unchain/toolkits/builtin/external_api/external_api.py:12` | subpackage | class |
| `GitToolkit` | `src/unchain/toolkits/builtin/git/git.py:14` | subpackage | class |
| `PlanToolkit` | `src/unchain/toolkits/builtin/plan/plan.py:192` | subpackage | class |
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

- `CoreToolkit`
- `ExternalAPIToolkit`
- `GitToolkit`
- `PlanToolkit`

### 最小调用示例

```python
obj = BuiltinToolkit(...)
obj.push_execution_context(...)
```

### `src/unchain/toolkits/builtin/core/core.py`

内置核心 toolkit，提供工作区代码读写、shell、网页抓取、LSP 查询和结构化用户问询能力。

## CoreToolkit

工作区作用域 toolkit，注册大多数编码 agent 默认需要的 9 个工具。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/unchain/toolkits/builtin/core/core.py:30` |
| 模块职责 | 编码、shell、web fetch、LSP 与结构化用户问询的核心内置 toolkit。 |
| 继承/协议 | `BuiltinToolkit` |
| 导出状态 | 通过所属子包 `__init__` 导出。 |
| 对象类型 | 类；公开。 |

### 构造表面

- `__init__(self, *, workspace_root: str | Path | None=None, workspace_roots: list[str | Path] | None=None)`

`workspace_root` 是单根的便捷写法；`workspace_roots` 接收多根列表。两者必须至少有一个能解析为可用目录，否则抛 `ValueError`。

### 注册的工具

9 个工具在 `__init__` 阶段全部 eagerly 注册，并对照 `toolkit.toml` 校验。

| 工具 | 签名 | 需要确认 | 说明 |
| --- | --- | --- | --- |
| `read` | `read(path: str, offset: int = 0, limit: int | None = None)` | 否 | 按绝对路径读取 UTF-8 文本，输出带行号，支持切片。会记录一个 freshness snapshot 给 `write`/`edit` 使用。 |
| `write` | `write(path: str, content: str)` | 是 | 创建或完全覆写 UTF-8 文本文件；已有文件必须先被完整读取，snapshot 失效则中止。 |
| `edit` | `edit(path: str, old_string: str, new_string: str, replace_all: bool = False)` | 是 | 在已读过的文件中替换唯一匹配（或 `replace_all=True` 时替换所有匹配）。 |
| `glob` | `glob(pattern: str, ...)` | 否 | 返回最多 200 条匹配 glob 的路径，按最近修改时间倒序。 |
| `grep` | `grep(pattern: str, globs: list[str] | None = None, mode: str = ...)` | 否 | UTF-8 文本正则搜索，支持 glob 过滤和分页输出模式。 |
| `web_fetch` | `web_fetch(url: str, extract: str | None = None)` | 是 | 抓取 HTTP(S) 页面；`extract` 切换原始内容或 runtime 配置的提取模型。 |
| `shell` | `shell(command: str, ...)` | 是（按风险） | 运行 shell 命令、轮询后台任务或终止任务。低风险命令跳过确认。 |
| `lsp` | `lsp(path: str, method: str, ...)` | 否 | 查询语言服务器（Python 或 TS/JS），支持 `goToDefinition`、`findReferences`、`hover`、`documentSymbol`、`workspaceSymbol`。 |
| `ask_user_question` | `ask_user_question(title, question, selection_mode, options, ...)` | n/a | 保留运行时工具：会暂停整个 run，由框架而非直接执行来满足。 |

### Runtime 协作者

- `LSPRuntime`（Python + TS/JS 语言服务器，按 `workspace_roots` 懒启动）。
- `ShellRuntime`（探测 `bash`/`zsh`/`sh`，确认前做风险分类）。
- `WebFetchService`（缓存响应；支持 runtime 配置的提取模型）。
- 每 session 一张 `_ReadSnapshot` 表 —— write/edit 拒绝操作未被完整读取或磁盘上已变更的文件。

### 生命周期与运行时职责

- 构造函数装载工作区根，实例化 runtime，并调用 `_register_tools()`。
- 工具方法走标准 `Toolkit.execute()`；需要确认的工具走 kernel 的 `ToolExecutionHarness`。
- `shutdown()`（继承自 `Toolkit`）会拆掉 LSP 和 shell runtime。

### 协作关系与关联类型

- `BuiltinToolkit`
- `ExternalAPIToolkit`
- `GitToolkit`
- `PlanToolkit`
- `MCPToolkit`

### 最小调用示例

```python
from unchain import Agent
from unchain.agent import ToolsModule
from unchain.toolkits import CoreToolkit

agent = Agent(
    name="coder",
    instructions="你是一个编码助手。",
    modules=(ToolsModule(tools=(CoreToolkit(workspace_root="."),)),),
)
```

### `src/unchain/toolkits/builtin/git/git.py`

工作区作用域 Git toolkit，用于 status、diff、stage、unstage 和 commit 工作流。

## GitToolkit

内置 toolkit，注册 5 个固定 argv 模板的 Git 工具。路径参数会按 workspace roots 校验，修改类操作都需要确认。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/unchain/toolkits/builtin/git/git.py:14` |
| 模块职责 | 带工作区路径校验的内置 Git 工作流 toolkit。 |
| 继承/协议 | `BuiltinToolkit` |
| 导出状态 | 从 `unchain.toolkits` 导出。 |
| 对象类型 | 类；公开。 |

### 构造表面

- `__init__(self, *, workspace_root: str | Path | None = None, workspace_roots: list[str | Path] | None = None) -> None`

`workspace_root` 是单根便捷写法；`workspace_roots` 接收多个允许的根目录。Git repo root 和路径参数都必须解析到这些根目录内。

### 注册的工具

| 工具 | 签名 | 需要确认 | 说明 |
| --- | --- | --- | --- |
| `git_status` | `git_status(cwd=".", include_untracked=True, max_output_chars=20000)` | 否 | 返回 branch/status 输出和结构化文件摘要。 |
| `git_diff` | `git_diff(cwd=".", staged=False, paths=None, context_lines=3, max_output_chars=50000)` | 否 | 返回 worktree 或 staged unified diff，以及每个文件的增删统计。 |
| `git_stage` | `git_stage(paths, cwd=".", max_output_chars=20000)` | 是 | stage 指定且校验通过的文件路径。 |
| `git_unstage` | `git_unstage(paths, cwd=".", max_output_chars=20000)` | 是 | 从 staging area 移除指定且校验通过的文件路径。 |
| `git_commit` | `git_commit(message, cwd=".", max_output_chars=20000)` | 是 | 只提交已 staged 内容，并提供 code-diff 确认预览。 |

### 最小调用示例

```python
from unchain.toolkits import GitToolkit

git = GitToolkit(workspace_root=".")
status = git.git_status()
diff = git.git_diff(staged=True)
```

### `src/unchain/toolkits/builtin/plan/plan.py`

规划 toolkit，用于创建、更新、读取、列出和定稿结构化实施计划。

## PlanToolkit

有状态 toolkit，注册 5 个规划工具。它不修改 `Agent.run`、`KernelLoop` 或 provider 协议；全部行为都通过普通工具调用完成。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/unchain/toolkits/builtin/plan/plan.py:192` |
| 模块职责 | 内置 planning toolkit，维护结构化 plan 状态并渲染 Markdown。 |
| 继承/协议 | `Toolkit` |
| 导出状态 | 从 `unchain.toolkits` 导出。 |
| 对象类型 | 类；公开。 |

### 构造表面

- `__init__(self, *, session_store: Any = None, session_id: str = "", workspace_root: str | Path | None = None) -> None`

`workspace_root` 是必需的。结构化 plan state 写入 `plans/<plan_id>.json`，用户可读 Markdown 写入 `plans/<plan_id>.md`。`session_store` 和 `session_id` 仍然作为兼容参数接受，但不会被读取或写入。

### 注册的工具

| 工具 | 签名 | 需要确认 | 说明 |
| --- | --- | --- | --- |
| `plan_start` | `plan_start(title, goal, constraints=None)` | 否 | 在 workspace 中创建 draft plan，并返回状态和 Markdown 文件位置。 |
| `plan_update` | `plan_update(plan_id, summary=None, steps=None, ...)` | 否 | 替换传入的结构化章节。步骤状态只能是 `pending`、`in_progress` 或 `completed`；同一计划最多一个 `in_progress`。 |
| `plan_read` | `plan_read(plan_id)` | 否 | 返回状态和 workspace Markdown 文件位置。 |
| `plan_finalize` | `plan_finalize(plan_id)` | 是 | 将 workspace plan 标记为 finalized。 |
| `plan_list` | `plan_list()` | 否 | 列出 workspace 中的 draft/finalized plans。 |

成功调用返回 `{"ok": True, "plan_id": ..., "status": ..., "revision": ..., "workspace_file": {"path": ..., "relative_path": ...}}`。tool result 不再嵌入 `plan`、`markdown`、`artifact`、`artifacts` 或 `proposed_plan`。错误返回 `{"ok": False, "error": ...}`，如果请求携带了 `plan_id`，错误载荷也会保留它。

### 规划工作流

当 agent 需要在探索需求时持续维护 design-first 计划，使用 `PlanToolkit`。工具的 prompt spec 会引导模型先探索上下文，随着事实变化更新结构化章节，并且只在计划 decision-complete 时调用 `plan_finalize`。

交互式规划推荐组合 `CoreToolkit` 与 `PlanToolkit`：`PlanToolkit` 不重复实现 `ask_user_question`；遇到会实质改变计划的关键决策时，使用 `CoreToolkit.ask_user_question` 让用户选择。

### 最小调用示例

```python
from unchain.toolkits import PlanToolkit

plans = PlanToolkit(workspace_root=".")
created = plans.plan_start(title="Auth rollout", goal="Plan the first auth rollout.")
plans.plan_update(
    created["plan_id"],
    steps=[{"step": "Add lifecycle tests", "status": "in_progress"}],
)
finalized = plans.plan_finalize(created["plan_id"])
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
- `CoreToolkit`
- `GitToolkit`
- `PlanToolkit`

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
- `CoreToolkit`
- `ExternalAPIToolkit`

### 最小调用示例

```python
obj = MCPToolkit(...)
obj.connect(...)
```
