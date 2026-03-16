# interaction_toolkit

`interaction_toolkit` 提供结构化用户交互能力。

## 用法

```python
from miso import interaction_toolkit

tk = interaction_toolkit()
```

## 工具清单

- `request_user_input`

## 设计意图

- 显式暴露用户交互相关的 tool schema
- 由 `broth` 负责 suspend / resume 等运行时语义
- 仅适用于支持 tool calling 的模型
