# Miso 概览

语言入口：[English](README.en.md) | [简体中文](README.zh-CN.md) | [首页](../README.md)

`miso` 是一个 Python agent framework。第二阶段重构后，对外 API 收口，低层模块也都按职责拆开。

## 正式导入方式

```python
from miso import Agent, Team
from miso.runtime import Broth
from miso.tools import Tool, Toolkit, ToolParameter, tool
from miso.toolkits import (
    AskUserToolkit,
    ExternalAPIToolkit,
    MCPToolkit,
    TerminalToolkit,
    WorkspaceToolkit,
)
from miso.schemas import ResponseFormat
from miso.memory import MemoryConfig, MemoryManager
from miso.input import media
```

## 包结构

```text
src/miso/
  agents/
  runtime/
  tools/
  toolkits/
  memory/
  input/
  workspace/
  schemas/
  _internal/
```

## 运行时分层

- `miso.agents`：高层 `Agent` / `Team`
- `miso.runtime`：低层 `Broth` runtime 与模型 payload 资源
- `miso.tools`：tool 定义、decorator、registry、catalog
- `miso.toolkits`：内置 toolkit 与 MCP bridge
- `miso.memory`：短期 / 长期记忆组件
- `miso.input`：human input 与 media helper
- `miso.schemas`：结构化输出模型

## 内置 Toolkits

- `WorkspaceToolkit`：文件、目录、多语言 syntax tree、行级编辑与 workspace pin
- `TerminalToolkit`：受限 shell 执行与持久 session
- `ExternalAPIToolkit`：基础外部 HTTP 调用
- `AskUserToolkit`：结构化向用户提问并 suspend / resume
- `MCPToolkit`：把 MCP server 暴露成 toolkit

## 快速示例

```python
from miso import Agent
from miso.toolkits import WorkspaceToolkit, TerminalToolkit

agent = Agent(
    name="coder",
    provider="openai",
    model="gpt-5",
    tools=[
        WorkspaceToolkit(workspace_root="."),
        TerminalToolkit(workspace_root=".", terminal_strict_mode=True),
    ],
)

messages, bundle = agent.run("检查这个仓库并说明结构。")
```

## 结构化输出

```python
from miso.runtime import Broth
from miso.schemas import ResponseFormat

runtime = Broth(provider="openai", model="gpt-5")
fmt = ResponseFormat(
    name="summary",
    schema={
        "type": "object",
        "properties": {"summary": {"type": "string"}},
        "required": ["summary"],
        "additionalProperties": False,
    },
)
messages, bundle = runtime.run("总结这个仓库。", response_format=fmt)
```

## 测试

```bash
./scripts/init_python312_venv.sh
./run_tests.sh
```
