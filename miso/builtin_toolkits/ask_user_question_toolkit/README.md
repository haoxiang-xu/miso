# ask_user_toolkit

`ask_user_toolkit` 提供结构化向用户提问能力。

## 用法

```python
from miso import ask_user_toolkit

tk = ask_user_toolkit()
```

## 工具清单

- `request_user_input`

## 设计意图

- 显式暴露向用户提问相关的 tool schema
- 由 `broth` 负责 suspend / resume 等运行时语义
- 仅适用于支持 tool calling 的模型
