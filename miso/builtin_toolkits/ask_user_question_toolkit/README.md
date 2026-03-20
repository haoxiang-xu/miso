# ask_user_toolkit

`ask_user_toolkit` 提供结构化向用户提问能力。
当存在多种都说得通的方案，而且不同选择会明显影响结果时，应该优先问用户，不要默认替用户做决定。

## 用法

```python
from miso import ask_user_toolkit

tk = ask_user_toolkit()
```

## 工具清单

- `request_user_input`

## 设计意图

- 显式暴露向用户提问相关的 tool schema
- 当存在多个合理 approach、产品方向、技术选型、交互方案或需求解释时，强烈建议模型先问用户
- 由 `broth` 负责 suspend / resume 等运行时语义
- 仅适用于支持 tool calling 的模型
