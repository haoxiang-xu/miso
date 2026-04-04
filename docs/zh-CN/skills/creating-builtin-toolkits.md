# 创建内置 Toolkit

`creating-builtin-toolkits` 主题的正式简体中文 skills 章节。

## 角色与边界

本章是为 unchain 新增或维护 builtin toolkit 的实现指南。

## 依赖关系

- builtin toolkit 建立在 `Toolkit` 或 `BuiltinToolkit` 上。
- manifest 由 `ToolkitRegistry` 校验。
- 运行时安全依赖正确的 workspace path 解析、manifest 元数据和 shutdown 行为。

## 核心对象

- `BuiltinToolkit`
- `Toolkit`
- `ToolkitRegistry`
- `WorkspaceToolkit`
- `TerminalToolkit`
- `ExternalAPIToolkit`
- `AskUserToolkit`

## 执行流与状态流

- 创建目录、实现、manifest 和 readme。
- 注册每个工具并确保 manifest/runtime 一致。
- 从包导出 toolkit 并验证 discovery。
- 保持包内 README 精简，并指向正式文档树。

## 配置面

- manifest 字段，如 `id`、`factory`、`readme`、`[[tools]]`。
- 每个 tool 的 `observe` 和 `requires_confirmation`。
- workspace root、icon 资源与 registry 发现路径。

## 扩展点

- 新增 builtin toolkit。
- 挂接 history optimizer 或自定义参数元数据。
- 当需要文件系统安全时使用 `BuiltinToolkit`。

## 常见陷阱

- 工具方法名和 manifest 条目必须精确一致。
- workspace 感知 toolkit 必须安全解析路径。
- factory 必须是可导入的零参 callable。

## 关联 class 参考

- [Toolkit Implementations](../api/toolkits.md)
- [Tool System API](../api/tools.md)

## 源码入口

- `src/unchain/toolkits/base.py`
- `src/unchain/toolkits/builtin/`
- `src/unchain/tools/registry.py`

## 详细的遗留参考

以下保留了原始仓库 skill 笔记，用于延续性与额外示例。规范副本现已迁入此文档树。

> 新增 builtin toolkit 的分步指南，覆盖所有常见陷阱。

## 目录结构

每个 builtin toolkit 位于 `src/unchain/toolkits/builtin/<toolkit_id>/` 下：

```text
src/unchain/toolkits/builtin/
└── my_toolkit/
    ├── __init__.py       # 重新导出 toolkit 类
    ├── my_toolkit.py     # Toolkit 实现
    ├── toolkit.toml      # Manifest (必需)
    ├── README.md         # Toolkit 级文档 (manifest 要求)
    └── icon.svg          # 可选自定义图标
```

## 步骤 1: 编写 Manifest (`toolkit.toml`)

manifest 声明元数据并列举 toolkit 暴露的每个工具。

```toml
[toolkit]
id = "my_toolkit"                              # 必填 -- 跨所有来源唯一的 ID
name = "My Toolkit"                            # 必填 -- 显示名称
description = "What this toolkit does."        # 必填
factory = "unchain.toolkits.builtin.my_toolkit:MyToolkit"  # 必填 -- 零参 callable → Toolkit
version = "1.0.0"                              # 可选
readme = "README.md"                           # 必填 -- 相对于本文件
icon = "folder"                                # 内置图标名称 或 .svg/.png 路径
color = "#eff6ff"                              # 使用内置图标名称时必填
backgroundcolor = "#2563eb"                    # 使用内置图标名称时必填
tags = ["builtin", "my-toolkit"]               # 可选

[display]
category = "builtin"                           # 可选 -- UI 分组
order = 50                                     # 可选 -- 值越小排越前
hidden = false                                 # 可选

[compat]
python = ">=3.9"                               # 可选
unchain = ">=0"                                   # 可选

[[tools]]
name = "do_something"                          # 必填 -- 必须精确匹配 Python 方法名
title = "Do Something"                         # 可选 -- 默认使用 name
description = "Explain what this tool does."   # 必填
observe = false                                # 可选 -- 执行后注入 observation
requires_confirmation = false                  # 可选 -- 执行前阻塞等待用户批准

[[tools]]
name = "do_another_thing"
title = "Do Another Thing"
description = "Explain this one too."
```

### 校验规则 (由 `ToolkitRegistry` 执行)

| 规则                                                                         | 违反时后果     |
| ---------------------------------------------------------------------------- | -------------- |
| 每个 `[[tools]].name` 必须匹配运行时 toolkit 中已注册的方法                   | discovery 错误 |
| 运行时已注册的工具不可在 `[[tools]]` 中缺失                                  | discovery 错误 |
| TOML 中的 `observe` 和 `requires_confirmation` 必须与 `Tool` 对象一致        | discovery 错误 |
| `toolkit.id` 在 builtin + local + plugins 中必须唯一                         | discovery 错误 |
| 图标资源路径必须在 toolkit 目录内                                             | 安全错误       |
| factory 必须是返回 `Toolkit` 实例的零参 callable                              | 实例化错误     |

## 步骤 2: 选择基类

### `BuiltinToolkit` -- 用于涉及文件系统的 toolkit

```python
from unchain.toolkits import BuiltinToolkit

class MyToolkit(BuiltinToolkit):
    def __init__(self, *, workspace_root: str | Path | None = None):
        super().__init__(workspace_root=workspace_root)
        # 在此注册工具
```

提供:

- `self.workspace_root` -- 已解析的 `Path` (默认为 cwd)
- `self._resolve_workspace_path(path)` -- **强制** 路径安全检查
- session 功能 (pin) 的执行上下文栈

### `Toolkit` -- 用于不需要 workspace 的 toolkit

```python
from unchain.tools import Toolkit

class MyToolkit(Toolkit):
    def __init__(self):
        super().__init__()
        # 在此注册工具
```

当 toolkit 没有文件系统依赖时使用 (如 `AskUserToolkit`)。

## 步骤 3: 注册工具

### 模式 A: `register_many` (最常见)

```python
def __init__(self, *, workspace_root=None):
    super().__init__(workspace_root=workspace_root)
    self._register_tools()

def _register_tools(self) -> None:
    self.register_many(
        self.do_something,
        self.do_another_thing,
    )
```

### 模式 B: 带元数据覆盖的 `register`

当需要 history optimizer 或覆盖自动推断的元数据时使用：

```python
def _register_tools(self) -> None:
    self.register(
        self.read_files,
        history_arguments_optimizer=self._compact_read_args,
        history_result_optimizer=self._compact_read_result,
    )
```

### 模式 C: 外部工具定义 (罕见)

用于保留的运行时工具 (如 `ask_user_question`)，schema 来自别处：

```python
reserved = build_ask_user_question_tool()
self.register(
    self.ask_user_question,
    name=reserved.name,
    description=reserved.description,
    parameters=reserved.parameters,
)
```

## 步骤 4: 实现工具方法

### 方法签名规则

```python
def do_something(self, path: str, max_chars: int = 20000) -> dict[str, Any]:
    """Short description of the tool (becomes the tool description).

    :param path: File path relative to workspace root.
    :param max_chars: Maximum characters to return.
    """
    target = self._resolve_workspace_path(path)   # ← 始终验证路径
    # ... 实现 ...
    return {"content": "...", "truncated": False}
```

- **`self` 自动跳过** -- 参数推断时不处理。
- **类型提示 → JSON schema 类型**: `str→string`, `int→integer`, `float→number`, `bool→boolean`, `list[T]→array`, `dict→object`。
- **默认值** 使参数在 schema 中变为可选。
- **docstring 首行** → 工具描述 (若未在 `register()` 中覆盖)。
- **`:param name:` 行** → 参数描述。

### 返回值约定

始终返回 `dict[str, Any]`：

```python
# 成功 -- 包含相关数据
return {"path": str(target), "content": content, "truncated": False}

# 错误 -- 使用 "error" key
return {"error": f"file not found: {target}"}

# 避免非 dict 返回 (它们会被自动包裹为 {"result": value}，
#    但显式 dict 对 LLM 更清晰)
return "some string"
```

**说明**: 项目中没有统一强制的 `"ok"` key 约定。有的 toolkit 使用 `{"ok": True, ...}` (ExternalAPI)，有的不用 (Workspace)。在单个 toolkit 内选定一种模式并保持一致。

### 错误处理模式

```python
def http_get(self, url: str, ...) -> dict[str, Any]:
    try:
        # ... 发起请求 ...
        return {"ok": True, "status_code": 200, "body": body}
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"HTTP {e.code}: {e.reason}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
```

工具异常也会被 `Tool.execute()` 捕获并包裹为 `{"error": str(exc), "tool": name}`，但显式的错误 dict 能给 LLM 提供更好的上下文。

## 步骤 5: 配置导出

### `my_toolkit/__init__.py`

```python
from .my_toolkit import MyToolkit

__all__ = ["MyToolkit"]
```

### `builtin/__init__.py` -- 添加到现有导出

```python
from .my_toolkit import MyToolkit  # 加入此行
```

### `toolkits/__init__.py` -- 添加到包导出

```python
from .builtin import MyToolkit  # 加入此行
```

这确保 `from unchain.toolkits import MyToolkit` 能正常使用。

## 步骤 6: 编写 README

保持简短 -- 机器可读的元数据在 toolkit.toml 中。

````markdown
# My Toolkit

`MyToolkit` does X.

## Usage

\```python
from unchain.toolkits import MyToolkit

tk = MyToolkit(workspace_root=".")
\```

## Included Tools

- `do_something`
- `do_another_thing`

## Design Constraints

- Only operates within `workspace_root`
- Does not do Y — combine with Z toolkit for that
````

## 常见错误

### 1. 忘记 `_resolve_workspace_path()`

```python
# 路径穿越漏洞 -- 用户可逃逸 workspace
target = self.workspace_root / path

# 安全 -- 解析符号链接、阻止逃逸
target = self._resolve_workspace_path(path)
```

这是一个 **安全要求**，而非建议。来自 LLM 的每个路径参数都必须通过此方法。

### 2. `[[tools]]` name 与方法名不匹配

```toml
[[tools]]
name = "doSomething"   # camelCase
```

```python
def do_something(self, ...):  # snake_case
```

TOML 中的 `name` 必须是 **精确的 Python 方法名**。

### 3. 已注册方法缺少 `[[tools]]` 条目

运行时注册的每个工具都必须在 `toolkit.toml` 中有对应的 `[[tools]]` 节。registry 会双向验证完整性。

### 4. Factory 需要参数

```toml
factory = "my_module:MyToolkit"
```

factory 在 discovery/实例化时以 **零参** 方式调用。如果你的 `__init__` 需要 `workspace_root`，框架会单独处理 -- factory 必须能以零参调用。用 `workspace_root=None` 配合默认值 `os.getcwd()`。

### 5. 忘记 `shutdown()`

如果 toolkit 持有资源 (session、连接、临时文件)，请覆盖 `shutdown()`：

```python
def shutdown(self) -> None:
    self._runtime.close_all()
```

框架在清理时调用 `shutdown()`。

### 6. History optimizer 未返回压缩形式

history optimizer 必须减小载荷大小。如果工具返回大量内容 (文件读取、API 响应)，请实现压缩：

```python
self.register(
    self.read_files,
    history_result_optimizer=self._compact_result,
)

def _compact_result(self, result: dict) -> dict:
    """将大量内容替换为摘要，用于对话历史。"""
    if len(result.get("content", "")) > 500:
        return {**result, "content": f"[{len(result['content'])} chars, truncated in history]"}
    return result
```

## 检查清单

- [ ] `toolkit.toml` 包含所有必填字段 (`id`, `name`, `description`, `factory`, `readme`)
- [ ] 每个 `[[tools]].name` 精确匹配一个 Python 方法
- [ ] 每个已注册方法都有 `[[tools]]` 条目
- [ ] `observe` 和 `requires_confirmation` 在 TOML 和代码之间一致
- [ ] 所有文件路径经过 `_resolve_workspace_path()`
- [ ] 工具方法返回 `dict[str, Any]`
- [ ] `__init__.py` 导出链完整 (toolkit → builtin → toolkits)
- [ ] README.md 存在且在 `toolkit.toml` 中被引用
- [ ] 若 toolkit 持有资源则实现了 `shutdown()`
- [ ] 图标资源保持在 toolkit 目录内

## 相关 Skills

- [architecture-overview.md](architecture-overview.md) -- toolkit 在系统中的位置
- [tool-system-patterns.md](tool-system-patterns.md) -- 工具定义、装饰器、参数推断
- [testing-conventions.md](testing-conventions.md) -- 如何测试 toolkit
