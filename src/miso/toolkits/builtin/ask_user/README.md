# Ask User Toolkit

`AskUserToolkit` 提供结构化向用户提问的保留工具。

## 用法

```python
from miso.toolkits import AskUserToolkit

tk = AskUserToolkit()
```

## 包含的能力

- `ask_user_question`

## 设计约束

- `ask_user_question` 是运行时保留工具，不应被直接本地执行
- suspend / resume 语义由 `Broth` 运行时处理
