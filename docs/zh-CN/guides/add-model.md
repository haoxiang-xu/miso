# 添加新模型

本指南说明如何在 unchain 框架中注册一个新的 LLM 模型，使其可被 agent 使用。

## 前提条件

- 了解模型的能力（上下文窗口大小、工具支持、模态等）
- 该模型必须由 unchain 已支持的 provider（OpenAI、Anthropic、Gemini、Ollama）提供服务。如果不是，请先按照[添加新 Provider](add-provider.md) 进行操作。

## 参考文件

| 文件 | 职责 |
|------|------|
| `src/unchain/runtime/resources/model_capabilities.json` | 模型注册表，含能力描述与约束 |
| `src/unchain/runtime/resources/model_default_payloads.json` | 每个模型的默认请求负载 |
| `src/unchain/schemas/models.py` | 模型命名常量 |

## 步骤

1. **阅读模型能力注册表。** 打开 `src/unchain/runtime/resources/model_capabilities.json`，研究现有条目的结构，理解所需字段。

2. **阅读默认负载。** 打开 `src/unchain/runtime/resources/model_default_payloads.json`，查看每个模型的默认请求参数定义方式。

3. **在 `model_capabilities.json` 中添加新条目**，包含以下字段：

   - `provider` -- 取值之一：`openai`、`anthropic`、`gemini`、`ollama`
   - `provider_model` -- 实际 API 模型 ID（如果与键名不同）
   - `max_context_window_tokens` -- 最大上下文窗口大小
   - `supports_tools` -- 是否支持工具/函数调用
   - `supports_response_format` -- 是否支持结构化响应格式
   - `supports_previous_response_id` -- 是否支持响应链接
   - `supports_reasoning` -- 是否支持扩展思考/思维链
   - `input_modalities` -- 支持的输入类型列表（如 `["text", "image"]`）
   - `input_source_types` -- 输入源格式（如 `["base64", "url"]`）
   - `allowed_payload_keys` -- 该模型接受的 provider 特定参数

4. **添加默认负载条目**，如果模型需要自定义默认参数（如 temperature、max_tokens），将其添加到 `model_default_payloads.json`。

5. **添加命名常量**（可选）。如果模型需要便捷的别名，将其添加到 `src/unchain/schemas/models.py`。

## 示例

在 `model_capabilities.json` 中添加新条目：

```json
{
  "my-new-model": {
    "provider": "openai",
    "provider_model": "my-new-model-2025-04",
    "max_context_window_tokens": 128000,
    "supports_tools": true,
    "supports_response_format": true,
    "supports_previous_response_id": false,
    "supports_reasoning": false,
    "input_modalities": ["text", "image"],
    "input_source_types": ["base64", "url"],
    "allowed_payload_keys": ["temperature", "max_tokens", "top_p"]
  }
}
```

## 测试

运行模型相关测试：

```bash
PYTHONPATH=src pytest tests/ -q --tb=short -k "model"
```

## 相关文档

- [添加新 Provider](add-provider.md) -- 如果模型需要新的 provider
- [运行时引擎](../skills/runtime-engine.md) -- 运行时如何解析模型能力
- [运行时 API 参考](../api/runtime.md) -- 运行时资源加载
