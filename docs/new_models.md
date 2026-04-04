# 新增模型配置

本文档描述了最近添加到unchain项目中的新模型配置。

## 新增模型

### 1. GPT-4o (`GPT_4O`)

OpenAI的GPT-4o模型配置，具有以下特性：

- **提供商**: OpenAI
- **模型名称**: `gpt-4o`
- **上下文窗口**: 128,000 tokens
- **支持功能调用**: ✓
- **支持响应格式**: ✓
- **支持推理**: ✗
- **输入模态**: 文本、图像
- **默认参数**:
  - max_tokens: 16,384
  - temperature: 0.7
  - top_p: 1
  - frequency_penalty: 0
  - presence_penalty: 0

### 2. Claude Sonnet 3.5 (`CLAUDE_SONNET_35`)

Anthropic的Claude 3.5 Sonnet模型配置，具有以下特性：

- **提供商**: Anthropic
- **模型名称**: `claude-3-5-sonnet-20241022`
- **上下文窗口**: 200,000 tokens
- **支持功能调用**: ✓
- **支持响应格式**: ✗
- **支持推理**: ✓
- **输入模态**: 文本、图像
- **默认参数**:
  - max_tokens: 32,000
  - temperature: 0.7
  - top_p: 1

### 3. Gemini Pro 1.5 (`GEMINI_PRO_15`)

Google的Gemini Pro 1.5模型配置，具有以下特性：

- **提供商**: Google
- **模型名称**: `gemini-1.5-pro`
- **上下文窗口**: 1,048,576 tokens (1M+)
- **支持功能调用**: ✓
- **支持响应格式**: ✗
- **支持推理**: ✗
- **输入模态**: 文本、图像、音频、视频
- **默认参数**:
  - max_output_tokens: 8,192
  - temperature: 0.7
  - top_p: 1
  - top_k: 40

## 使用方法

### 基本导入和使用

```python
from unchain.schemas.models import GPT_4O, CLAUDE_SONNET_35, GEMINI_PRO_15

# 选择一个模型
model = GPT_4O

# 获取模型信息
print(f"模型名称: {model.name}")
print(f"提供商: {model.capabilities.provider}")
print(f"上下文窗口: {model.capabilities.max_context_window_tokens}")
print(f"支持工具: {model.capabilities.supports_tools}")

# 获取默认参数
default_params = model.default_payload.to_dict()
print(f"默认参数: {default_params}")
```

### 模型序列化

```python
# 转换为字典
model_dict = GPT_4O.to_dict()

# 从字典创建模型
custom_model = ModelConfiguration.from_dict(
    name="custom-model",
    capabilities_data=model_dict['capabilities'],
    payload_data=model_dict['default_payload']
)
```

### 创建自定义模型

```python
from unchain.schemas.models import ModelConfiguration, ModelCapabilities, ModelDefaultPayload

# 创建自定义模型配置
custom_model = ModelConfiguration(
    name="my-custom-model",
    capabilities=ModelCapabilities(
        provider="custom",
        max_context_window_tokens=8192,
        supports_tools=True,
        input_modalities=["text"]
    ),
    default_payload=ModelDefaultPayload(
        payload={
            "temperature": 0.7,
            "max_tokens": 2048
        }
    )
)
```

## 模型比较

| 模型 | 提供商 | 上下文窗口 | 工具支持 | 推理支持 | 多模态 |
|------|--------|------------|----------|----------|--------|
| GPT-4o | OpenAI | 128K | ✓ | ✗ | 文本+图像 |
| Claude Sonnet 3.5 | Anthropic | 200K | ✓ | ✓ | 文本+图像 |
| Gemini Pro 1.5 | Google | 1M+ | ✓ | ✗ | 文本+图像+音频+视频 |

## 注意事项

1. **上下文窗口**: Gemini Pro 1.5具有最大的上下文窗口（1M+ tokens），适合处理长文档。
2. **多模态能力**: Gemini Pro 1.5支持最多的输入模态，包括音频和视频。
3. **推理能力**: 目前只有Claude Sonnet 3.5明确支持推理功能。
4. **响应格式**: 只有GPT-4o支持结构化响应格式。

## 示例代码

完整的使用示例请参考 `examples/new_models_usage.py` 文件。

## 测试

新模型的测试用例位于 `tests/test_new_models.py`，包含：
- 模型配置验证
- 序列化/反序列化测试
- 基本功能测试

运行测试：
```bash
./run_tests.sh tests/test_new_models.py
```