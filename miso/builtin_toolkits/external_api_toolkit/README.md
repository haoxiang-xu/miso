# external_api_toolkit

`external_api_toolkit` 提供对外部 HTTP API 的基础请求能力。

## 用法

```python
from miso import external_api_toolkit

tk = external_api_toolkit(workspace_root=".")
```

## 工具清单

- `http_get`
- `http_post`

## 设计意图

- 提供最小可用的 HTTP GET/POST 能力。
- 返回结果内含 `status_code`、`headers` 与 `body`，并可控制最大返回长度。
