# 工具系统模式

`tool-system-patterns` 主题的正式简体中文 skills 章节。

## 角色与边界

本章覆盖框架工具抽象，从原始 callable 推断，到 manifest 发现，再到运行时激活。

## 依赖关系

- `Tool` 与 `Toolkit` 是本地执行原语。
- `ToolkitRegistry` 读取 manifest 并把元数据与运行时对象做一致性校验。
- `ToolkitCatalogRuntime` 在发现结果之上叠加运行时激活/停用能力。

## 核心对象

- `Tool`
- `Toolkit`
- `ToolParameter`
- `ToolkitRegistry`
- `ToolkitCatalogRuntime`
- `ToolConfirmationRequest`
- `ToolConfirmationResponse`

## 执行流与状态流

- 把 callable 包装成 `Tool`。
- 把它注册进 `Toolkit`。
- 通过 manifest 从 builtin/local/plugin 三种来源发现 toolkit。
- 按需允许模型在运行时通过 catalog 激活/停用 toolkit。

## 配置面

- `observe` 与 `requires_confirmation` 标志。
- history payload optimizer。
- registry 的 local roots、enabled plugins 与 catalog managed IDs。

## 扩展点

- 创建 builtin toolkit。
- 通过 entry points 发布 plugin toolkit。
- 自定义历史压缩和工具元数据。

## 常见陷阱

- 工具名冲突会阻止同时激活。
- manifest 与运行时元数据必须一致。
- `@tool` 返回的是 `Tool`，不是原始函数。

## 关联 class 参考

- [Tool System API](../api/tools.md)
- [Toolkit Implementations](../api/toolkits.md)
- [Builtin Toolkit Guide](creating-builtin-toolkits.md)

## 源码入口

- `src/unchain/tools/tool.py`
- `src/unchain/tools/toolkit.py`
- `src/unchain/tools/registry.py`
- `src/unchain/tools/catalog.py`

## 详细的遗留参考

以下保留了原始仓库 skill 笔记，用于延续性与额外示例。规范副本现已迁入此文档树。

> 工具的定义、注册、发现和管理方式。覆盖 `Tool`、`Toolkit`、`@tool` 装饰器、参数推断、确认流程和动态 catalog。

## 核心抽象

```text
Tool          单个 callable 加元数据 (name, description, JSON schema 参数)
Toolkit       Tool 的 dict -- 注册、查找、按名称执行
@tool         把函数转为 Tool 对象的装饰器
ToolParameter 手动参数定义 (很少需要 -- 自动推断能处理大多数情况)
```

## Tool 类解剖

```python
from unchain.tools import Tool

t = Tool(
    name="greet",
    description="Say hello.",
    func=lambda name: {"message": f"Hello, {name}!"},
    parameters=[ToolParameter(name="name", description="Who to greet", type_="string", required=True)],
    observe=False,               # 若为 True，Broth 在执行后注入 observation turn
    requires_confirmation=False, # 若为 True，Broth 在执行前暂停等待用户批准
)

result = t.execute({"name": "Alice"})
# → {"message": "Hello, Alice!"}
```

通常不会直接构造 `Tool` -- 用 `@tool` 或 `Toolkit.register()` 替代。

## 参数推断

参数从函数签名和 docstring **自动推断**。只有边界情况才需要手动 `ToolParameter`。

### 类型提示 → JSON Schema

| Python 类型                 | JSON Schema `type`              |
| --------------------------- | ------------------------------- |
| `str`                       | `"string"`                      |
| `int`                       | `"integer"`                     |
| `float`                     | `"number"`                      |
| `bool`                      | `"boolean"`                     |
| `list[T]`, `tuple[T, ...]`  | `"array"` with `items` from `T` |
| `dict[K, V]`                | `"object"`                      |
| `T \| None` (`Optional[T]`) | type of `T` (None 被剥离)      |

### 推断来源

```python
def read_files(self, paths: list[str], max_chars_per_file: int = 20000) -> dict[str, Any]:
    """Read UTF-8 text files from the workspace.

    :param paths: File paths relative to workspace root.
    :param max_chars_per_file: Maximum characters to return per file.
    """
```

提取内容:

| 来源               | 提取结果                                                            |
| -------------------- | ------------------------------------------------------------------- |
| 函数名             | → `tool.name` = `"read_files"`                                             |
| docstring 首行     | → `tool.description` = `"Read UTF-8 text files..."`                        |
| 类型提示           | → 参数类型 (`array`, `integer`)                                            |
| 默认值             | → 参数 `required` 标志 (`paths` 必填, `max_chars_per_file` 可选)           |
| `:param ...:` 行   | → 参数描述                                                          |
| `self` 参数        | → **自动跳过**                                                      |

### 支持的 Docstring 风格

```python
# 风格 1: Sphinx 风格
"""Description.

:param path: File path.
:param max_chars: Limit.
"""

# 风格 2: Google 风格
"""Description.

Args:
    path: File path.
    max_chars: Limit.
"""
```

两者生成相同的参数 schema。

## `@tool` 装饰器

### 裸用法 (最常见)

```python
from unchain.tools import tool

@tool
def greet(name: str) -> dict:
    """Say hello to someone."""
    return {"message": f"Hello, {name}!"}

# greet 现在是 Tool 对象，不是普通函数
assert isinstance(greet, Tool)
assert greet.name == "greet"
```

### 带选项

```python
@tool(name="custom_name", observe=True, requires_confirmation=True)
def greet(name: str) -> dict:
    """Say hello to someone."""
    return {"message": f"Hello, {name}!"}
```

### 可用选项

| 选项                          | 类型                  | 默认值               | 效果                                      |
| ----------------------------- | --------------------- | -------------------- | ----------------------------------------- |
| `name`                        | `str`                 | 函数名               | 覆盖工具名称                              |
| `description`                 | `str`                 | docstring 首行       | 覆盖描述                                  |
| `parameters`                  | `list[ToolParameter]` | 自动推断             | 覆盖所有参数                              |
| `observe`                     | `bool`                | `False`              | 执行后注入 observation turn               |
| `requires_confirmation`       | `bool`                | `False`              | 暂停等待用户批准                          |
| `history_arguments_optimizer` | callable              | `None`               | 压缩对话历史中的参数                      |
| `history_result_optimizer`    | callable              | `None`               | 压缩对话历史中的结果                      |

**注意**: `@tool` 返回 `Tool` 对象。如果你在其他地方需要原始函数，在装饰前保留一份引用。

## `observe` 与 `requires_confirmation`

### `observe=True`

工具执行后，Broth 会额外发起一次 **LLM 调用**，将工具结果作为上下文注入。LLM 有机会"观察"结果并在用户看到中间结果前决定下一步。

适用于：输出需要解读的工具 (代码分析、搜索结果)。

### `requires_confirmation=True`

执行前，Broth **暂停** 并向 UI 发射 `ToolConfirmationRequest`。只有在 `ToolConfirmationResponse` 到达后才恢复执行。

适用于：破坏性操作 (文件删除、数据库写入、不可逆操作)。

### 确认流程

```text
1. LLM 请求调用 requires_confirmation=True 的工具
2. Broth 构建 ToolConfirmationRequest(tool_name, call_id, arguments)
3. 框架通过 callback 将请求发送到 UI
4. UI 向用户显示确认对话框
5. 用户批准/拒绝 (可选修改参数)
6. ToolConfirmationResponse(approved, modified_arguments, reason) 发回
7. 若批准 → 工具执行 (若有修改则使用修改后的参数)
   若拒绝 → 跳过工具，向 LLM 发送错误消息
```

```python
from unchain.tools import ToolConfirmationRequest, ToolConfirmationResponse

# Request (由框架构建)
req = ToolConfirmationRequest(
    tool_name="write_file",
    call_id="call_abc123",
    arguments={"path": "important.py", "content": "updated text"},
    description="Write important.py",
)

# Response (来自 UI)
resp = ToolConfirmationResponse(approved=True)
# 或: ToolConfirmationResponse(approved=False, reason="Not safe")
# 或: ToolConfirmationResponse(approved=True, modified_arguments={"path": "temp.py"})
```

## Toolkit 注册模式

### 向 Toolkit 添加工具

```python
from unchain.tools import Toolkit

tk = Toolkit()

# 注册一个 callable (自动包裹为 Tool)
tk.register(my_function)

# 带元数据覆盖注册
tk.register(my_function, name="custom_name", observe=True)

# 注册一个预构建的 Tool 对象
tk.register(some_tool_object)

# 批量注册
tk.register_many(func_a, func_b, func_c)
```

### 执行工具

```python
result = tk.execute("tool_name", {"arg1": "value"})
# → {"result": ...} 或 {"error": ...}
```

### 合并 toolkit

来自多个 toolkit 的工具会合并为一个供 runtime 使用：

```python
agent = Agent(
    tools=[
        WorkspaceToolkit(workspace_root="."),
        TerminalToolkit(workspace_root="."),
        my_custom_tool,          # 单个 Tool 或 callable
    ],
)
# Agent 将所有工具合并为一个 Toolkit 传给 Broth
```

**冲突检测**: 如果两个 toolkit 注册了同名工具，catalog 系统会拒绝同时激活。在单个 toolkit 内，后注册的会覆盖先注册的。

## Toolkit 发现 (三种来源)

`ToolkitRegistry` 从三种来源发现 toolkit：

| 来源        | 位置                                            | 时机                                    |
| ----------- | ----------------------------------------------- | --------------------------------------- |
| **Builtin** | `src/unchain/toolkits/builtin/*/toolkit.toml`      | 始终 (除非 `include_builtin=False`)     |
| **Local**   | `ToolRegistryConfig.local_roots` 中的目录       | 配置时                                  |
| **Plugins** | `entry_points(group="unchain.toolkits")`           | 已安装包声明时                          |

### Plugin entry point 约定

```toml
# 在 plugin 的 pyproject.toml 中
[project.entry-points."unchain.toolkits"]
my_plugin = "my_package.toolkit:MyToolkit"
```

entry point **名称必须匹配** plugin 的 `toolkit.toml` 中的 `toolkit.id`。

## 动态 Catalog (运行时激活)

`ToolkitCatalogRuntime` 允许 agent 在运行时激活/停用 toolkit：

```python
agent.enable_toolkit_catalog(
    managed_toolkit_ids=["workspace", "terminal", "external_api"],
    always_active_toolkit_ids=["workspace"],
)
```

这会向 agent 注入 5 个 catalog 管理工具：

| Catalog 工具          | 用途                                         |
| --------------------- | -------------------------------------------- |
| `toolkit_list`        | 列出可用的 (非隐藏) toolkit                  |
| `toolkit_describe`    | 获取 toolkit 的元数据 + readme               |
| `toolkit_activate`    | 激活 toolkit (含冲突检测)                    |
| `toolkit_deactivate`  | 停用 (always-active 除外)                    |
| `toolkit_list_active` | 列出当前活跃的 toolkit ID                    |

**State token**: catalog 状态通过 `build_continuation_state()` / `extract_toolkit_catalog_token()` 跨运行暂停保存，恢复时不丢失激活状态。

## History Payload 优化

对于产生大量输出的工具 (文件读取、API 响应)，注册 optimizer 来缩减对话历史：

```python
self.register(
    self.read_files,
    history_arguments_optimizer=self._compact_args,
    history_result_optimizer=self._compact_result,
)

def _compact_result(self, result: dict) -> dict:
    content = result.get("content", "")
    if len(content) > 500:
        return {**result, "content": f"[{len(content)} chars, see tool output above]"}
    return result
```

optimizer 接收原始 dict 并必须返回一个 (可能更小的) dict。它 **只应用于历史轮次**，而非当前轮次 -- LLM 在当前轮次始终看到完整结果。

## 常见陷阱

1. **`@tool` 返回 `Tool`，不是函数** -- 对装饰过的函数调用 `greet("Alice")` 会通过 `Tool.execute()` 执行，而非原始函数。

2. **`self` 自动跳过** -- 不要在 `ToolParameter` 列表或 docstring 参数中包含 `self`。

3. **非 dict 返回值会被包裹** -- `return "hello"` 变成 `{"result": "hello"}`。始终返回显式的 dict。

4. **工具名冲突阻止激活** -- catalog 会拒绝激活两个共享工具名的 toolkit。

5. **同一工具上的 `observe` + `requires_confirmation`** -- 可以同时使用，但确认先发生，观察在执行后发生。

6. **参数默认值** -- 有默认值的参数在 JSON schema 中变为可选。没有默认值的参数是必填的。

## 相关 Skills

- [creating-builtin-toolkits.md](creating-builtin-toolkits.md) -- 端到端 toolkit 创建指南
- [architecture-overview.md](architecture-overview.md) -- 工具系统在整体中的位置
- [runtime-engine.md](runtime-engine.md) -- Broth 如何执行工具调用
