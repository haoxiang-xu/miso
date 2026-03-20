# External API Toolkit

`ExternalAPIToolkit` 提供基础的外部 HTTP 请求能力。

## 用法

```python
from miso.toolkits import ExternalAPIToolkit

tk = ExternalAPIToolkit(workspace_root=".")
```

## 包含的能力

- `http_get`
- `http_post`

## 设计约束

- 面向通用 HTTP 调用，不负责鉴权策略管理
- 返回值保持简单的请求/响应摘要结构
