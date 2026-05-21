# SAP Hyperspace Provider

`HyperspaceModelIO` 让 unchain agent 连接到 **SAP Hyperspace** —— 一个内部的 LLM 代理,在可配置的 base URL(默认 `http://localhost:6655/anthropic`)上暴露与 Anthropic 兼容的 Messages API。

因为 Hyperspace 使用 Anthropic 的 wire 协议,`HyperspaceModelIO` 继承自 `AnthropicModelIO`,只覆盖了默认的 `client_factory` 让它指向配置的 base URL。所有 Anthropic 特性都自动继承:

- 流式 `token_delta` / `reasoning` 事件(通过 `request.callback`)
- 扩展思考块(extended thinking)
- 工具调用 round-trip + prompt caching(`cache_control: ephemeral`)
- 带缓存信息的 token 用量统计

## 安装

`anthropic` Python SDK 已经是 unchain 的运行时依赖 —— 无需额外安装。

## 快速上手(Agent 高阶 API)

```python
from unchain import Agent

agent = Agent(
    name="translator",
    provider="hyperspace",
    model="hyperspace--claude-opus-4-6",
    api_key="<your-hyperspace-key>",   # 或设置 HYPERSPACE_API_KEY 环境变量
    instructions="You are a careful technical translator.",
)

result = agent.run("Translate 'hello world' to Spanish.")
print(result.messages[-1]["content"])
```

`_create_hyperspace` 读取的环境变量:

| 变量 | 用途 | 默认值 |
|------|------|--------|
| `HYPERSPACE_API_KEY` | 作为 `x-api-key` 发送 | 必填 |
| `HYPERSPACE_BASE_URL` | 覆盖默认的 Anthropic-compatible endpoint | `http://localhost:6655/anthropic` |

## 直接使用 provider(低阶)

```python
from unchain.providers import HyperspaceModelIO
from unchain.kernel import ModelTurnRequest

io = HyperspaceModelIO(
    model="hyperspace--claude-opus-4-6",
    api_key="<your-hyperspace-key>",
    base_url="http://localhost:6655/anthropic",  # 可选
)

events: list = []
turn = io.fetch_turn(
    ModelTurnRequest(
        messages=[
            {"role": "system", "content": "You are a translator."},
            {"role": "user", "content": "Translate 'hello' to French."},
        ],
        callback=events.append,
        emit_stream=True,
        run_id="demo",
    )
)
print(turn.final_text)  # "bonjour"
```

## 已注册模型

| 模型 key(unchain) | provider_model(发往 Hyperspace) | 说明 |
|---------------------|-----------------------------------|------|
| `hyperspace--claude-opus-4-6` | `anthropic--claude-opus-4-6` | tools、thinking |
| `hyperspace--claude-opus-4-7` | `anthropic--claude-opus-4-7` | tools、thinking |
| `hyperspace--claude-sonnet-4-6` | `anthropic--claude-sonnet-4-6` | tools、thinking |
| `hyperspace--claude-haiku-4-5` | `anthropic--claude-haiku-4-5` | tools、thinking |

`hyperspace--*` 前缀避免与原生 Anthropic 的 registry 条目冲突;`provider_model` 字段在发往 wire 时把模型名重写为 SAP 的 `anthropic--*` 命名。

## 鉴权

`anthropic` SDK 默认就会发送 `X-Api-Key: <key>` 和 `anthropic-version: 2023-06-01` —— 这正是 Hyperspace 的要求,无需自定义任何头。

## 测试

完整的测试套见 `tests/test_hyperspace_model_io.py`(请求构建、流式、工具调用、扩展思考、参数校验)。

```bash
PYTHONPATH=src pytest tests/test_hyperspace_model_io.py -v
```
