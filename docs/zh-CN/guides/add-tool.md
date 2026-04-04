# 添加新工具

本指南说明如何向现有 toolkit 添加新工具，或创建独立工具。工具是 agent 在执行过程中可以调用的函数，用于与外部世界交互。

## 前提条件

- 理解工具系统（参见[工具系统模式](../skills/tool-system-patterns.md)）
- 如果是向 toolkit 中添加工具，需要熟悉该 toolkit 的结构（参见[创建内置 Toolkit](../skills/creating-builtin-toolkits.md)）

## 参考文件

| 文件 | 职责 |
|------|------|
| `src/unchain/tools/tool.py` | `Tool` 类与参数推断 |
| `src/unchain/tools/toolkit.py` | `Toolkit` 基类 |
| `src/unchain/tools/execution.py` | `ToolExecutionHarness` -- 运行时工具调用方式 |
| `src/unchain/tools/confirmation.py` | 工具确认门控逻辑 |
| `src/unchain/tools/messages.py` | 工具结果消息格式化 |

## 步骤

### 向现有 toolkit 添加工具

1. **阅读 toolkit 源码**，位于 `src/unchain/toolkits/builtin/<toolkit>/`，理解其结构和约定。

2. **添加工具函数**，使用正确的类型标注和文档字符串。文档字符串的第一行将作为展示给 LLM 的工具描述。

3. **注册工具**，通过 toolkit 的 `__init__` 方法中调用 `self.register()` 完成注册。

### 创建独立工具

1. **使用 `@tool` 装饰器**，将普通函数转换为独立工具。

2. **通过类型标注定义参数。** 工具系统会从你的类型注解和文档字符串自动推断出供 LLM 使用的 JSON schema。

## 模板

### 使用装饰器的独立工具

```python
from unchain.tools import tool


@tool
def my_tool(param1: str, param2: int = 10) -> dict:
    """Tool description. First line becomes the tool description for the LLM.

    Args:
        param1: Description of param1
        param2: Description of param2
    """
    return {"result": "..."}
```

### 带确认门控的工具

对于执行破坏性或敏感操作的工具，在执行前要求用户确认：

```python
Tool(
    name="dangerous_tool",
    func=my_func,
    requires_confirmation=True,
)
```

### 带观察注入的工具

对于输出需要作为观察消息注入回对话中的工具：

```python
Tool(
    name="observe_tool",
    func=my_func,
    observe=True,
)
```

## 测试

针对特定工具运行测试：

```bash
PYTHONPATH=src pytest tests/ -q --tb=short -k "<tool_name>"
```

## 相关文档

- [工具系统模式](../skills/tool-system-patterns.md) -- 工具系统概念详解
- [创建内置 Toolkit](../skills/creating-builtin-toolkits.md) -- toolkit 打包与注册
- [工具 API 参考](../api/tools.md) -- 工具系统完整 API 接口
- [Toolkit API 参考](../api/toolkits.md) -- toolkit 注册与发现
