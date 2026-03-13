# terminal_toolkit

`terminal_toolkit` 是一个只暴露 terminal action 的内置工具包。  
它使用共享的受限 terminal runtime，可与 `workspace_toolkit` 按需组合。

## 用法

```python
from miso import terminal_toolkit

tk = terminal_toolkit(
    workspace_root=".",
    terminal_strict_mode=True,
)
```

## 工具清单

- `terminal_exec`
- `terminal_session_open`
- `terminal_session_write`
- `terminal_session_close`

## 设计意图

- 让 terminal 权限可以独立挂载，不必默认暴露文件编辑能力。
- 保持独立的 terminal capability，避免默认暴露文件编辑能力。
- 方便按职责拆分多个 toolkit，由 agent 按需组合。
