# 添加新的内置 Toolkit

本指南将引导你为 unchain 框架创建一个新的内置 toolkit。Toolkit 是一组相关工具的打包集合，带有清单文件和生命周期管理。

## 前提条件

- 理解工具系统（参见[工具系统模式](../skills/tool-system-patterns.md)）
- 熟悉 toolkit 打包方式（参见[创建内置 Toolkit](../skills/creating-builtin-toolkits.md)）

## 参考文件

| 文件 | 职责 |
|------|------|
| `src/unchain/toolkits/builtin/code/` | 复杂 toolkit 示例（代码操作） |
| `src/unchain/toolkits/builtin/ask_user/` | 简单 toolkit 示例（单个工具） |
| `src/unchain/tools/tool.py` | `Tool` 类 |
| `src/unchain/tools/toolkit.py` | `Toolkit` 基类 |
| `src/unchain/toolkits/__init__.py` | Toolkit 导出与发现 |

## 步骤

1. **学习现有 toolkit** 的实现模式：
   - **复杂型：** `src/unchain/toolkits/builtin/code/` -- 包含代码操作的多工具 toolkit
   - **简单型：** `src/unchain/toolkits/builtin/ask_user/` -- 最小化的单工具 toolkit

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
```

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
