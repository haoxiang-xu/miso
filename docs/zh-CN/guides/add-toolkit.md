# 添加新的内置 Toolkit

本指南将引导你为 unchain 框架创建一个新的内置 toolkit。Toolkit 是一组相关工具的打包集合，带有清单文件和生命周期管理。

## 前提条件

- 理解工具系统（参见[工具系统模式](../skills/tool-system-patterns.md)）
- 熟悉 toolkit 打包方式（参见[创建内置 Toolkit](../skills/creating-builtin-toolkits.md)）

## 参考文件

| 文件 | 职责 |
|------|------|
| `src/unchain/toolkits/builtin/core/` | 复杂 toolkit 示例（代码操作） |
| `src/unchain/toolkits/builtin/external_api/` | 较小的内置 toolkit 示例 |
| `src/unchain/toolkits/builtin/plan/` | 进程内有状态 toolkit 示例 |
| `src/unchain/tools/tool.py` | `Tool` 类 |
| `src/unchain/tools/toolkit.py` | `Toolkit` 基类 |
| `src/unchain/toolkits/__init__.py` | Toolkit 导出与发现 |

## 步骤

1. **学习现有 toolkit** 的实现模式：
   - **复杂型：** `src/unchain/toolkits/builtin/core/` -- 包含代码操作的多工具 toolkit
   - **较小示例：** `src/unchain/toolkits/builtin/external_api/` -- 聚焦型内置 toolkit 示例
   - **有状态示例：** `src/unchain/toolkits/builtin/plan/` -- 进程内状态和需要确认的定稿工具

2. **创建 toolkit 目录：**
   ```
   src/unchain/toolkits/builtin/<name>/
   ```

3. **创建 `toolkit.toml` 清单文件**（参见下方模板）。

4. **创建 `__init__.py`**，包含 toolkit 类：
   - 继承 `unchain.tools` 中的 `Toolkit`
   - 在 `__init__` 中通过 `self.register()` 注册工具
   - 使用 `@tool` 装饰器或直接构造 `Tool()` 对象
   - 为所有工具参数添加正确的类型标注和文档字符串

5. **在 `src/unchain/toolkits/__init__.py` 中导出**，使 toolkit 可被发现。

6. **编写测试**，放在 `tests/test_<name>_toolkit.py`。

## 模板

### toolkit.toml

```toml
[toolkit]
name = "<name>"
description = "<description>"
version = "0.1.0"

[[artifact_kinds]]
kind = "example.report"
display_name = "Example report"
description = "Rendered from immutable artifact snapshots."
icon = "bar_chart"
fallback_renderer = "markdown"
```

`fallback_renderer` 必须是 `markdown`、`text`、`table`、`kv`、`log`、`link` 或 `json`。`icon` 可以是 PuPu 内置 icon id，也可以是 toolkit 包内相对路径的 `.svg` / `.png` 静态资源。不要在 artifact runtime event 里内联 SVG、HTML、CSS、React 组件或 icon hint；event 只应该携带不可变 snapshot 数据。

如果 toolkit 会修改 workspace 内的文本文件，单独发 artifact event 不足以进入 run-level 文件总结或 undo。可用执行上下文存在时，应把文件变更上报给 workspace change tracker：

```python
context = self.current_execution_context
tracker = getattr(context, "workspace_changes", None) if context is not None else None
if tracker is not None:
    tracker.record_text_file_change(
        str(path),
        before_text,
        after_text,
        operation="modified",
        tool_name="my_tool",
        call_id=context.call_id,
        turn_id=context.turn_id,
    )
```

workspace change tracker 会生成一个 run 级别的 `workspace_change_set` artifact，里面包含 net diff 和安全 restore 元数据。toolkit 自己的 artifact 仍然适合做语义 UI，但只有上报给 tracker 的文件变更才会参与 net diff 和 undo。

### \_\_init\_\_.py

```python
from unchain.tools import Tool, Toolkit, tool


class MyToolkit(Toolkit):
    """My toolkit description."""

    name = "<name>"

    def __init__(self):
        super().__init__()

        @tool
        def my_tool(param: str) -> dict:
            """Tool description for the LLM.

            Args:
                param: Description of param
            """
            return {"result": "..."}

        self.register(my_tool)
```

### 目录结构

```
src/unchain/toolkits/builtin/<name>/
    __init__.py      # Toolkit class
    toolkit.toml     # Manifest
```

## 测试

运行 toolkit 测试：

```bash
PYTHONPATH=src pytest tests/test_<name>_toolkit.py -v --tb=short
```

## 相关文档

- [创建内置 Toolkit](../skills/creating-builtin-toolkits.md) -- toolkit 设计深入指南
- [工具系统模式](../skills/tool-system-patterns.md) -- 工具与 toolkit 的交互方式
- [Toolkit API 参考](../api/toolkits.md) -- 完整 toolkit API 接口
- [工具 API 参考](../api/tools.md) -- Tool 类详情
