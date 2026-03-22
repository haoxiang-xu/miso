# Terminal Toolkit

`TerminalToolkit` 提供受限的 terminal 执行与持久 session 能力。

## 用法

```python
from miso.toolkits import TerminalToolkit

tk = TerminalToolkit(
    workspace_root=".",
    terminal_strict_mode=True,
)
```

## 包含的能力

- `terminal_exec`
- `terminal_session_open`
- `terminal_session_write`
- `terminal_session_close`

## 设计约束

- 只暴露 terminal 行为，不包含文件编辑工具
- 需要文件编辑时，应与 `WorkspaceToolkit` 组合使用
