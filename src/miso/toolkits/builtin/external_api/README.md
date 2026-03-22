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
- `git_status`
- `git_log`
- `git_diff`
- `git_add`
- `git_commit`
- `git_checkout`
- `git_branch`

## 设计约束

- 面向通用 HTTP 调用，不负责鉴权策略管理
- 返回值保持简单的请求/响应摘要结构
- Git 工具仅用于读取状态/日志/差异输出，不做写操作
- 写操作的 Git 工具需要人工确认后执行
