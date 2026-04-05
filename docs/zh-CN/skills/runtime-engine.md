# 运行时引擎

`runtime-engine` 主题的正式简体中文 skills 章节。

## 角色与边界

本章说明 `Broth` 运行时、规范化的 provider turn 类型、callback 事件、workspace pin 注入以及暂停/恢复语义。

## 依赖关系

- `Broth` 协调 provider adapter、memory、toolkit、人类输入流和结构化输出。
- `ToolCall`、`ProviderTurnResult`、`TokenUsage`、`ToolExecutionOutcome` 是统一的运行时载荷类型。
- toolkit catalog 状态由 runtime 跨暂停保存。

## 核心对象

- `Broth`
- `ToolCall`
- `ProviderTurnResult`
- `TokenUsage`
- `ToolExecutionOutcome`

## 执行流与状态流

- 准备规范化消息并注入 pinned context。
- 抓取一个 provider turn 并规范化工具请求。
- 执行工具、处理确认或人类输入，并按需运行 observation turn。
- 在运行结束时提交 memory，并返回消息与包含 `status`、token 统计以及可选人类输入/continuation 状态的 bundle。

## 配置面

- provider/model/api key。
- 默认 payload 与 capability 资源文件。
- context window 覆盖、response format、callback、continuation hook。

## 扩展点

- 在 `runtime/providers/` 添加 provider 分发函数。
- 通过 `ResponseFormat` 扩展结构化输出处理。
- 在 runtime 实例上动态挂载或移除 toolkit。

## 常见陷阱

- observation turn 会消耗 iteration 预算。
- callback 是同步执行的。
- provider SDK 为懒加载，缺失依赖会在调用时失败。

## 关联 class 参考

- [Runtime API](../api/runtime.md)
- [Toolkits API](../api/toolkits.md)
- [Input/Workspace/Schema API](../api/input-workspace-schemas.md)

## 源码入口

- `src/unchain/runtime/engine.py`
- `src/unchain/runtime/payloads.py`
- `src/unchain/runtime/providers/`

## Broth 实践

`Broth` 是底层执行运行时。它不持有 agent 身份、默认指令或持久配置。它的职责更窄且更偏操作性：接收一个已准备好的请求、执行 provider turn、执行工具、处理暂停与恢复，并返回规范化的 conversation 加 bundle。

这个边界是有意为之的。`Agent` 回答"这个 agent 被配置成什么？"的问题，而 `Broth` 回答"这次具体运行如何执行？"的问题。正因如此，`Agent.run()` 每次都创建新的 `Broth`，而不是复用一个长生命周期的 runtime 实例。

## 当前执行流

1. `run()` 将输入消息规范化，校验模型能力是否支持当前输入模态，并把规范消息投影成 OpenAI、Anthropic、Gemini 或 Ollama 各自需要的 provider 特定格式。
2. 如果同时存在 `memory_manager` 和 `session_id`，runtime 在进入循环前会执行 memory prepare，将摘要历史、检索到的长期上下文和上下文窗口裁剪结果注入请求。
3. `_run_loop()` 解析当前可见的 toolkit，发起一次 provider turn，并将 provider 响应规范化为 `ProviderTurnResult`，使循环保持 provider 无关。
4. 若模型发出工具调用，runtime 执行工具、应用确认门，或在需要人类输入时提前返回 `awaiting_human_input`。标记了 `observe=True` 的工具还会触发额外的 observation turn，对最近的工具结果做简短复核。
5. 当某一轮不再产生工具调用时，runtime 应用结构化输出解析、构建 bundle、提交 memory 并返回最终 conversation。

## 设计说明

当前实现把 memory 视为 `run()` 边界上的能力，而非每轮迭代的中心状态机。这保持了 runtime 在无 memory 时可用、避免了每轮额外的摘要和提取成本，并防止了暂停前半成品状态被提交。

## 详细的遗留参考

以下保留了原始仓库 skill 笔记，用于延续性与额外示例。规范副本现已迁入此文档树。

> `Broth` 执行循环、provider 抽象、observation 注入、确认暂停、callback 事件和结构化输出。

## Broth -- 核心运行时

`Broth` 是底层引擎，负责协调 LLM 调用、工具执行、memory 集成和事件发射。`Agent` 在每次 `run()` 时创建新的 `Broth`。

```python
from unchain.runtime import Broth
from unchain.toolkits import CoreToolkit

runtime = Broth(provider="openai", model="gpt-5")
runtime.add_toolkit(CoreToolkit(workspace_root="."))
messages, bundle = runtime.run("Inspect the repo.")
```

### 关键构造参数

| 参数             | 类型          | 默认值   | 用途                                                         |
| ---------------- | ------------- | -------- | ------------------------------------------------------------ |
| `provider`       | `str`         | 必填     | `"openai"`, `"anthropic"`, `"gemini"`, `"ollama"`            |
| `model`          | `str`         | 必填     | 模型标识 (如 `"gpt-5"`, `"claude-opus-4-20250918"`)          |
| `api_key`        | `str \| None` | `None`   | 未提供时使用环境变量                                         |
| `base_url`       | `str \| None` | `None`   | 自定义端点 (用于 Ollama、代理)                               |
| `max_iterations` | `int`         | `6`      | 最大工具调用循环迭代次数                                     |
| `system_prompt`  | `str`         | `""`     | 会话前置的系统消息                                           |

### 返回值

```python
messages, bundle = runtime.run(...)

# messages: list[dict] -- 包含工具调用/结果的完整会话
# bundle: dict -- 元数据
#   bundle["consumed_tokens"]       -- 总 token 用量
#   bundle["stop_reason"]           -- 循环结束原因
#   bundle["artifacts"]             -- 收集的 artifact (如有)
#   bundle["toolkit_catalog_token"] -- catalog 状态 (若 catalog 已启用)
```

## 执行循环 (逐步)

```text
Broth.run(messages, toolkit, response_format, max_iterations, ...)
│
│  for iteration in 1..max_iterations:
│
│  ┌─ 步骤 1: 准备上下文 ──────────────────────────────────┐
│  │ - memory.prepare_messages(session_id)                   │
│  │ - 注入 workspace pin context 为系统消息                  │
│  │ - 应用上下文窗口策略 (裁剪/摘要)                         │
│  └─────────────────────────────────────────────────────────┘
│
│  ┌─ 步骤 2: LLM 调用 ────────────────────────────────────┐
│  │ - _fetch_once(messages, tools, response_format)          │
│  │ - 分发到 provider SDK (OpenAI/Anthropic/等)              │
│  │ - 返回 ProviderTurnResult:                               │
│  │   { assistant_message, tool_calls[], token_usage }       │
│  │ - 发射事件: token_delta, message_published               │
│  └─────────────────────────────────────────────────────────┘
│
│  ┌─ 步骤 3: 工具执行 ────────────────────────────────────┐
│  │ for each tool_call in result.tool_calls:                 │
│  │                                                          │
│  │   if requires_confirmation:                              │
│  │     → 构建 ToolConfirmationRequest                       │
│  │     → 发射事件，暂停等待用户响应                          │
│  │     → 若拒绝: 跳过工具，向 LLM 发送错误                  │
│  │     → 若批准: 继续 (可能使用修改后的参数)                │
│  │                                                          │
│  │   result = toolkit.execute(tool_name, arguments)         │
│  │   → 发射事件: tool_result                                │
│  │                                                          │
│  │   if observe=True:                                       │
│  │     → _observation_turn(messages + tool_result)          │
│  │     → 额外 LLM 调用来"观察"结果                          │
│  │     → observation 追加到 messages                        │
│  └─────────────────────────────────────────────────────────┘
│
│  ┌─ 步骤 4: MEMORY 提交 ─────────────────────────────────┐
│  │ - memory.commit_messages(session_id, full_conversation)  │
│  │ - 存储对话轮次、压缩历史、提取长期事实                    │
│  └─────────────────────────────────────────────────────────┘
│
│  ┌─ 步骤 5: 循环检查 ────────────────────────────────────┐
│  │ - 本轮无 tool_calls? → 退出 (完成)                       │
│  │ - 达到最大迭代次数? → 退出 (带警告)                      │
│  │ - 请求了人类输入? → 暂停                                 │
│  └─────────────────────────────────────────────────────────┘
│
▼
返回 (messages, bundle)
```

## Provider 抽象

Broth 内部使用 **规范消息格式**。provider 特定的 SDK 为懒加载。

### 支持的 Provider

| Provider    | SDK                   | 说明                                   |
| ----------- | --------------------- | -------------------------------------- |
| `openai`    | `openai`              | 默认，测试最充分                       |
| `anthropic` | `anthropic`           | Claude 模型                            |
| `gemini`    | `google-generativeai` | Gemini 模型                            |
| `ollama`    | `openai` (兼容)       | 通过 OpenAI 兼容 API 的本地模型        |

### 模型能力

模型能力在 `src/unchain/runtime/resources/` 下的 JSON 资源文件中声明。这些文件定义每个模型支持的特性：

```json
{
  "gpt-5": {
    "supports_tools": true,
    "supports_vision": true,
    "supports_structured_output": true,
    "context_window": 128000,
    "max_output_tokens": 16384
  }
}
```

运行时通过 `load_model_capabilities()` 加载。

### 默认 Payload

provider 特定的默认值 (temperature, top_p 等) 同样在资源 JSON 文件中，通过 `load_default_payloads()` 加载。

### 添加新 Provider

1. 创建 `src/unchain/runtime/providers/my_provider.py`
2. 按照现有模式实现 provider 分发函数
3. 在资源 JSON 中添加模型能力
4. 在资源 JSON 中添加默认 payload
5. 在 `engine.py` 分发逻辑中注册 provider 名称

provider 模块是 **懒加载** 的 -- 仅在使用 `provider="my_provider"` 时才 import。

## Callback 事件

Broth 在整个执行过程中通过 callback 函数发射事件。这为 UI 流式输出、日志记录和可观测性提供支持。

```python
def my_callback(event: dict) -> None:
    print(f"[{event['type']}] {event.get('data', '')}")

messages, bundle = runtime.run("task", callback=my_callback)
```

### 事件类型

| 事件类型                    | 触发时机                    | 载荷                                |
| --------------------------- | --------------------------- | ----------------------------------- |
| `run_started`               | 运行开始                    | `session_id`, `iteration`           |
| `token_delta`               | 收到流式 token              | `delta`, `role`                     |
| `message_published`         | assistant 消息完成          | 完整消息 dict                       |
| `tool_call_started`         | 工具执行前                  | `tool_name`, `call_id`, `arguments` |
| `tool_result`               | 工具执行后                  | `tool_name`, `call_id`, `result`    |
| `tool_confirmation_request` | 工具需要批准                | `ToolConfirmationRequest`           |
| `observation_started`       | observation LLM 调用前      | `tool_name`                         |
| `observation_complete`      | observation LLM 调用后      | observation 消息                    |
| `memory_commit`             | memory 提交后               | `session_id`                        |
| `run_completed`             | 运行正常结束                | `stop_reason`, `iterations`         |
| `run_error`                 | 运行因错误结束              | `error`                             |
| `human_input_request`       | 需要人类输入                | 请求详情                            |
| `human_input_response`      | 收到人类输入                | 响应详情                            |
| `iteration_started`         | 新循环迭代开始              | `iteration` 序号                    |
| `context_window_usage`      | 上下文准备后                | token 计数                          |
| `summary_generated`         | 摘要生成后                  | 摘要文本                            |
| `long_term_extracted`       | 事实提取后                  | profile 更新                        |

**注意**: 并非每次运行都会发射所有事件。事件取决于配置 (memory、工具、确认)。

## 确认暂停与恢复

当调用了 `requires_confirmation=True` 的工具时：

```text
Broth.run()
  ├── LLM 请求工具调用
  ├── 工具标记了 requires_confirmation
  ├── ToolConfirmationRequest 通过 callback 发射
  ├── run() 暂停 -- 返回部分状态
  │
  │   ← 外部: UI 显示确认对话框
  │   ← 外部: 用户批准/拒绝
  │
  ├── Agent.resume_human_input(response)
  │   └── 带响应恢复 Broth
  ├── 若批准: 工具执行
  ├── 若拒绝: 向 LLM 发送错误，循环继续
  └── run() 继续或返回最终结果
```

**toolkit catalog 状态** 通过 state token 跨暂停保存。

## 结构化输出 (Response Format)

强制 LLM 返回匹配 schema 的 JSON：

```python
from unchain.runtime import Broth
from unchain.schemas import ResponseFormat

fmt = ResponseFormat(
    name="analysis",
    schema={
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "score": {"type": "integer"},
        },
        "required": ["summary", "score"],
        "additionalProperties": False,
    },
)

runtime = Broth(provider="openai", model="gpt-5")
messages, bundle = runtime.run("Analyze this code.", response_format=fmt)
# 最后一条消息的 content 保证是匹配 schema 的有效 JSON
```

**注意**: 并非所有模型都支持结构化输出。请检查 `model_capabilities["supports_structured_output"]`。

## Workspace Pin 注入

每次 LLM 调用前，Broth 会检查 session store 中是否有 **pinned 文件**。如有，则将其内容作为系统消息注入：

````text
[system] Pinned file context: src/main.py (lines 10-50)
```python
def main():
    ...
````

```

Pin 由 `CoreToolkit` 工具 (`pin_file_context`, `unpin_file_context`) 管理。限制：

| 限制                       | 值     |
|---------------------------|--------|
| 每个 session 最大 pin 数   | 8      |
| 最大 pin 总字符数          | 16,000 |
| 单个全文件 pin 最大字符数  | 12,000 |

Pin 对编辑具有 **弹性** -- 它们使用文本锚点和指纹在文件修改后重新定位内容。

## 常见陷阱

1. **每次运行使用新 Broth** -- `Agent.run()` 每次创建新的 `Broth`。不要尝试在运行之间复用或重新配置 `Broth` 实例。

2. **`max_iterations` 包含 observation turn** -- 如果有标记了 `observe=True` 的工具，每次 observation 消耗一次迭代。使用多个 observable 工具时应设置更高的 `max_iterations`。

3. **Provider SDK 懒加载** -- 第一次调用 provider 时触发 import。缺失 SDK (`pip install openai`) 会在调用时报错，而非 import 时。

4. **Callback 是同步的** -- 事件 callback 会阻塞执行循环。保持 callback 轻量或将工作卸载到队列。

5. **结构化输出 + 工具** -- 部分 provider 不支持同时使用 `response_format` 和 tool calling。引擎通过拆分最终 turn 来处理此情况。

6. **Token 统计是近似的** -- `bundle["consumed_tokens"]` 中报告的 token 用量取决于 provider 精度。用于预算控制，不用于计费。

## 相关 Skills

- [architecture-overview.md](architecture-overview.md) -- 系统级视图
- [tool-system-patterns.md](tool-system-patterns.md) -- 工具执行详情
- [memory-system.md](memory-system.md) -- MemoryManager 如何与 Broth 集成
- [agent-and-team.md](agent-and-team.md) -- Agent 如何创建和配置 Broth
```
