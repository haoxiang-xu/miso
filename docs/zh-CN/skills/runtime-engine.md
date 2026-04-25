# 运行时引擎

`runtime-engine` 主题的正式简体中文 skills 章节。

## 角色与边界

本章说明 `KernelLoop` 引擎、`RuntimeHarness` 扩展协议、`ModelIO` provider 边界、规范化的 run-result 类型，以及暂停/恢复语义。

## 依赖关系

- `KernelLoop` 协调 harness 阶段、模型 turn、工具执行和 run-result 装配。
- `ModelIO` 是 provider（OpenAI、Anthropic、Ollama、Gemini）实现的协议。kernel 从来不直接 import 厂商 SDK。
- `RuntimeHarness` 是 per-phase 扩展面。memory、optimizer、retry、subagent、tool execution、tool prompting 全部通过 harness 实现。
- `RunState` 是单次 run 的可变 scratch；`KernelRunResult` 是不可变返回。

## 核心对象

- `KernelLoop`
- `RuntimeHarness` / `RuntimePhase` / `HarnessContext`
- `ModelIO` / `ModelTurnRequest`
- `ToolCall` / `ModelTurnResult` / `TokenUsage` / `KernelRunResult`
- `LegacyBrothModelIO`（兼容用）

## 执行流与状态流

- 构造 `KernelLoop(model_io=...)`。
- 用 `register_harness(...)` 注册一个或多个 harness。
- 可选 `attach_memory(KernelMemoryRuntime)` 接 memory commit。
- 调 `run(messages, ...)`；loop 反复跑 `step_once()` 直到完成或暂停。
- 暂停时 loop 返回 `status="awaiting_human_input"` 的 `KernelRunResult` 加一份 `continuation` payload；把两个都喂给 `resume_human_input()` 继续。

## 配置面

- Provider/model 选择发生在 `ModelIO` 构造时。
- Per-run 选项通过 kernel 的 `run()` 参数传（max iterations、response format、callback、payload 默认值）。
- Harness 组合在跑 `Agent` 时由 `AgentBuilder` 完成；直接用 kernel 的代码自己手动注册 harness。

## 常见陷阱

- Observation turn 计入迭代预算。
- callback 在 loop 内同步执行；耗时工作要外抛。
- Provider SDK 懒加载；缺 SDK 时 `fetch_turn()` 才报错，不是 import 时。
- `Broth` **不再**是 runtime —— 只剩 `LegacyBrothModelIO` 这个适配器，让老代码路径能挂进新 kernel。

## 关联 class 参考

- [Runtime API](../api/runtime.md)
- [Toolkits API](../api/toolkits.md)
- [Tool System API](../api/tools.md)

## 源码入口

- `src/unchain/kernel/loop.py`
- `src/unchain/kernel/harness.py`
- `src/unchain/kernel/state.py`
- `src/unchain/kernel/types.py`
- `src/unchain/providers/model_io.py`

## KernelLoop 实践

`KernelLoop` 是底层执行 runtime。它不持有 agent 身份、默认指令或 module。它的职责更窄、更偏操作：接受规范化请求、跑模型 turn、执行工具、dispatch harness 阶段、处理暂停与恢复，并返回 `KernelRunResult`。

这个边界是有意为之的。`Agent` 回答 "this agent 配置成什么样？"，而 `KernelLoop` 回答 "这一次 run 怎么执行？" —— 这就是为什么 `Agent.run()` 每次都新建 `KernelLoop` 而不是复用一个。

```python
from unchain.kernel import KernelLoop
from unchain.providers import OpenAIModelIO

loop = KernelLoop(model_io=OpenAIModelIO(model="gpt-5"))
loop.register_harness(my_harness)
result = loop.run(messages=[{"role": "user", "content": "Hello"}])
```

日常使用优先 `Agent.run()`。直接用 `KernelLoop` 只在嵌入式场景（不要 agent 层）才需要。

## 当前执行流

1. `run()` 规范化输入消息，按模型能力校验模态支持，并为这次迭代构造 `RunState`。
2. loop 在每个模型 turn 前后 dispatch harness 走完 8 个阶段（完整列表见 `architecture-overview.md`）。
3. `ModelIO.fetch_turn(request)` 返回 `ModelTurnResult`，含 assistant 消息、工具调用、token 计数。
4. 如果模型发出工具调用，`ToolExecutionHarness` 执行它们。需要确认的工具会让 loop 提前返回 `status="awaiting_human_input"`。
5. 标了 `observe=True` 的工具会在 `after_tool_batch` 触发额外的 observation turn。
6. 当一轮不再产生工具调用时，loop 应用结构化输出解析、commit memory、返回 `KernelRunResult`。

## 设计要点

- Memory 以 harness 对的形式集成（bootstrap/before-model 召回 + before-commit 写）。不带 memory 的 run 直接省掉 `MemoryModule`。
- Retry 是包在 `ModelIO.fetch_turn()` 外的（见 `unchain.retry`），从来不重试已经流到下游的内容。
- Provider 特定投影（规范消息 → SDK 形状）完全在每个 `ModelIO` 实现内部，kernel 保持厂商无关。

## Provider 抽象

Provider 实现 `ModelIO`：

```python
class ModelIO(Protocol):
    provider: str
    def fetch_turn(self, request: ModelTurnRequest) -> ModelTurnResult: ...
```

### 内置实现

| Provider    | 类                  | SDK                   | 备注                         |
| ----------- | -------------------- | --------------------- | ---------------------------- |
| `openai`    | `OpenAIModelIO`      | `openai`              | 默认，最稳                   |
| `anthropic` | `AnthropicModelIO`   | `anthropic`           | Claude 模型                  |
| `ollama`    | `OllamaModelIO`      | `openai` 兼容         | 本地模型                     |
| `gemini`    | （在 providers/）    | `google-generativeai` | 懒加载                       |

### 模型能力

模型能力声明在 `src/unchain/runtime/resources/` 下的 JSON 资源文件，描述每个模型支持哪些特性：

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

### 加新 provider

1. 创建 `src/unchain/providers/my_provider.py`。
2. 实现一个带 `fetch_turn()` 的 `ModelIO` 子类。
3. 在 `src/unchain/runtime/resources/` 下加 capabilities 和默认 payload。
4. 要么直接把实例传给 `Agent(model_io_factory=...)`，要么在 `ModelIOFactoryRegistry` 里注册一个 factory。

provider 模块**懒加载** —— 只有真正构造 model IO 时才 import SDK。

## Callback 事件

harness 和 loop 通过 `Agent.run()` / `KernelLoop.run()` 传入的 `callback` 发出事件。这就是 UI 流式输出、日志、可观测性的支撑。

```python
def my_callback(event: dict) -> None:
    print(f"[{event['type']}] {event.get('data', '')}")

result = agent.run("task", callback=my_callback)
```

### 常见事件类型

| 事件类型                    | 触发时机                    | Payload                             |
| --------------------------- | --------------------------- | ----------------------------------- |
| `run_started`               | run 开始                    | `session_id`, `iteration`           |
| `token_delta`               | 收到流式 token              | `delta`, `role`                     |
| `message_published`         | assistant 消息完成          | 完整消息 dict                       |
| `tool_call_started`         | 工具执行前                  | `tool_name`, `call_id`, `arguments` |
| `tool_result`               | 工具执行后                  | `tool_name`, `call_id`, `result`    |
| `tool_confirmation_request` | 工具需要批准                | `ToolConfirmationRequest`           |
| `observation_started`       | observation turn 前         | `tool_name`                         |
| `observation_complete`      | observation turn 后         | observation 消息                    |
| `memory_commit`             | memory commit 后            | `session_id`                        |
| `run_completed`             | run 正常结束                | `stop_reason`, `iterations`         |
| `run_error`                 | run 出错结束                | `error`                             |
| `human_input_request`       | 需要人类输入                | 请求详情                            |
| `human_input_response`      | 收到人类输入                | 响应详情                            |
| `iteration_started`         | 新一轮迭代开始              | `iteration` 数字                    |
| `context_window_usage`      | 上下文准备后                | token 计数                          |
| `summary_generated`         | 摘要后                      | 摘要文本                            |
| `long_term_extracted`       | 抽事实后                    | profile 更新                        |

**注意**: 不是每个事件每次 run 都会触发。具体看你配了哪些 module 和 harness。

## 确认暂停与恢复

调用 `requires_confirmation=True` 的工具时：

```text
KernelLoop.run()
  ├── LLM 请求工具调用
  ├── on_tool_call 阶段：ToolExecutionHarness 构建 ToolConfirmationRequest
  ├── on_suspend 阶段触发；loop 返回 KernelRunResult(status="awaiting_human_input", continuation=...)
  │
  │   ← 外部：UI 显示确认对话框
  │   ← 外部：用户批准/拒绝
  │
  ├── Agent.resume_human_input(continuation=..., response=...)
  │   └── 在 on_resume 阶段重新进入 loop，response 在手
  ├── 若批准：执行工具（参数若被改则用改后的）
  ├── 若拒绝：错误发给 LLM，loop 继续
  └── run() 继续或返回最终结果
```

toolkit catalog 与 discovery 状态通过 harness 的 `on_suspend` / `on_resume` checkpoint 在这次往返中存活。

## 结构化输出（Response Format）

强制 LLM 返回匹配 schema 的 JSON：

```python
from unchain import Agent
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

result = agent.run("Analyze this code.", response_format=fmt)
# result.messages[-1] 的 content 必然是匹配 schema 的合法 JSON
```

**注意**: 不是所有模型都支持结构化输出。看 `model_capabilities["supports_structured_output"]`。

## 常见陷阱

1. **每次 run 都新 kernel** —— `Agent.run()` 每次都构造新的 `KernelLoop`。除非你在没有 `Agent` 的场景嵌入 kernel，否则不要复用单个 loop 跨 run。

2. **`max_iterations` 包含 observation turn** —— 标了 `observe=True` 的工具每触发一次就吃一轮迭代。如果你重度依赖可观察工具，把 `max_iterations` 调大。

3. **Provider SDK 懒加载** —— 第一次调用某 provider 才触发 import。缺 SDK（`pip install openai`）在 `fetch_turn()` 时报错，不是 import 时。

4. **callback 是同步的** —— 事件 callback 阻塞 loop。保持轻快或把工作 queue 出去。

5. **结构化输出 + 工具** —— 某些 provider 不能同时支持 `response_format` 和工具调用。对应的 `ModelIO` 实现会通过拆 final turn 来处理。

6. **Token 计数是近似** —— `KernelRunResult` 里的 token 用量看 provider 准确度。用来做预算，别用来计费。

7. **`Broth` 已经不在 runtime 路径上** —— 你 grep 它会找到 `kernel/model_io.py` 里的 `LegacyBrothModelIO` 和老的 `runtime/` 包。这两个只为迁移而存在；新代码直接面向 `ModelIO`。

## 相关 skills

- [architecture-overview.md](architecture-overview.md) — 系统级视图
- [tool-system-patterns.md](tool-system-patterns.md) — 工具执行细节
- [memory-system.md](memory-system.md) — memory harness 怎么挂进 loop
- [agent-and-team.md](agent-and-team.md) — Agent 怎么构造 kernel
