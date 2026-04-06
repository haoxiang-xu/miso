# 添加新的 LLM Provider

本指南将引导你为 unchain 框架添加一个新的 LLM provider 支持。Provider 是 SDK 层面的集成，使 unchain agent 能够与特定的 LLM 服务通信。

## 前提条件

- 已安装并可访问该 provider 的 Python SDK
- 理解该 provider 的流式 API 和工具调用格式
- 熟悉 `ModelIO` 抽象层（参见[架构总览](../skills/architecture-overview.md)）

## 参考文件

| 文件 | 职责 |
|------|------|
| `src/unchain/providers/model_io.py` | `_NativeModelIOBase` 及现有 provider 实现（OpenAI、Anthropic、Ollama） |
| `src/unchain/agent/model_io.py` | `ModelIOFactoryRegistry` -- 将 provider 名称映射到 `ModelIO` 工厂函数 |
| `src/unchain/tools/messages.py` | Provider 特定的工具结果消息构建器 |
| `src/unchain/runtime/resources/model_capabilities.json` | 模型注册表 |
| `src/unchain/runtime/resources/model_default_payloads.json` | 每个模型的默认负载 |
| `pyproject.toml` | 项目依赖 |

## 步骤

1. **学习 provider 抽象层。** 阅读 `src/unchain/providers/model_io.py`，理解：
   - `_NativeModelIOBase` -- 带有模型能力解析的基类
   - 现有实现：`OpenAIModelIO`、`AnthropicModelIO`、`OllamaModelIO`

2. **学习注册机制。** 阅读 `src/unchain/agent/model_io.py`，了解 `ModelIOFactoryRegistry` 如何将 provider 名称映射到工厂函数。

3. **学习消息构建器。** 阅读 `src/unchain/tools/messages.py`，查看各 provider 的工具结果格式化方式。

4. **创建新的 `ModelIO` 类**，添加到 `src/unchain/providers/model_io.py` 中：
   - 继承 `_NativeModelIOBase`
   - 设置 `provider = "<provider_name>"`
   - 实现 `fetch_turn(self, request: ModelTurnRequest) -> ModelTurnResult`
   - 处理流式传输并发出 `token_delta` 事件
   - 解析 provider 响应格式中的工具调用
   - 跟踪输入/输出 token 用量

5. **在 `ModelIOFactoryRegistry` 中注册**，位于 `src/unchain/agent/model_io.py`，将 provider 名称解析到你的新类。

6. **添加 provider 特定的消息构建器**，位于 `src/unchain/tools/messages.py`：
   - 实现 `build_tool_result_message(tool_call, tool_result)`，按照 provider 的预期格式构造工具结果。

7. **添加模型条目**到 `src/unchain/runtime/resources/model_capabilities.json`，注册该 provider 所服务的模型。

8. **添加默认负载**到 `src/unchain/runtime/resources/model_default_payloads.json`。

9. **添加 SDK 依赖**到 `pyproject.toml`。

10. **编写冒烟测试**，放在 `tests/test_<provider>_smoke.py`，使用 fake client 模式（参考现有测试中的 `FakeOpenAIClient` / `FakeAnthropicClient`）。

## 模板

```python
# In src/unchain/providers/model_io.py

class MyProviderModelIO(_NativeModelIOBase):
    provider = "my_provider"

    async def fetch_turn(self, request: ModelTurnRequest) -> ModelTurnResult:
        # 1. Build the provider-specific request from request.messages + request.tools
        # 2. Call the provider SDK (streaming)
        # 3. Yield token_delta events for each streamed chunk
        # 4. Parse tool_calls from the response
        # 5. Return ModelTurnResult with messages, tool_calls, and token counts
        ...
```

## 测试

运行冒烟测试：

```bash
PYTHONPATH=src pytest tests/ -q --tb=short
```

## 相关文档

- [添加新模型](add-model.md) -- 添加 provider 后注册模型
- [架构总览](../skills/architecture-overview.md) -- provider 在执行流中的位置
- [工具 API 参考](../api/tools.md) -- 工具消息格式详情
