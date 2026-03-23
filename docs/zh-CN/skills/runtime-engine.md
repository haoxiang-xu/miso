# Runtime Engine

`runtime-engine` 主题的正式简体中文 skills 章节。

## 角色与边界

本章说明 `Broth` 运行时、规范化的 provider turn 类型、callback 事件、workspace pin 注入以及暂停/恢复语义。

## 依赖关系

- `Broth` 协调 provider adapter、memory、toolkits、人类输入流和结构化输出。
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

- 在 `runtime/providers/` 添加 provider 分发。
- 通过 `ResponseFormat` 扩展结构化输出。
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

- `src/miso/runtime/engine.py`
- `src/miso/runtime/payloads.py`
- `src/miso/runtime/providers/`

## Broth 是什么

`Broth` 是低层运行时执行器。它不负责保存 agent 的身份、默认指令或长期配置；它负责的是把一次已经组装好的请求稳定地执行完：准备上下文、发起 provider turn、执行工具、处理暂停与恢复、并在结束时返回统一的 conversation 和 bundle。

从职责边界上看，`Broth` 关心的是“这一轮怎么跑”，而不是“这个 agent 是谁”。这也是为什么高层 `Agent` 每次 `run()` 都会创建新的 `Broth`，而不是长期复用同一个 runtime 实例。配置放在 `Agent`，执行状态机放在 `Broth`，两者的边界会更清晰。

## 当前实现里的运行链路

1. `run()` 先把输入消息规范化成内部的 canonical message 结构，校验 model capability 是否支持当前输入模态，再按 provider 投影成 OpenAI、Anthropic、Gemini 或 Ollama 各自需要的消息格式。
2. 如果同时启用了 `memory_manager` 和 `session_id`，runtime 会在真正进入主循环前做一次 memory prepare，把历史摘要、长期记忆检索结果和上下文裁剪结果注入到当前输入里。
3. `_run_loop()` 每一轮都会先解析当前可见的 toolkits，再向 provider 发起一次请求。provider 返回的结果会统一收敛成 `ProviderTurnResult`，因此上层循环不需要感知不同 SDK 的差异。
4. 如果模型返回了 tool calls，runtime 会执行工具、走确认门、或者在需要人类输入时提前返回 `awaiting_human_input`。对于标记了 `observe=True` 的工具，还会追加一个 observation turn，对最后一次工具结果做一次简短复核。
5. 当某一轮不再产生新的 tool calls 时，runtime 才进入收尾阶段：应用结构化输出解析、构建 bundle、提交 memory，并返回最终 conversation。

## 设计取舍

当前实现把 memory 设计成挂在 `run()` 边界上的能力，而不是主循环里的中心状态机。这样做有三个原因：

- memory 是可选能力。没有 `memory_manager` 或没有 `session_id` 时，`Broth` 仍然应该退化成纯粹的 provider/tool runtime。
- memory prepare 和 memory commit 都可能触发额外的摘要或提取逻辑，把它们放进每一轮循环会显著增加延迟和成本。
- 暂停与恢复要求执行过程尽量可重放。如果在人类输入前就提交 memory，容易把半成品状态写进存储层，并在恢复后出现重复或不一致。

## 详细说明

本章与英文版保持相同的阅读顺序，但把重点放在结构、调用链和对象边界上；API 级细节请与相邻的参考页配套阅读。

- Agents API 参考: `../api/agents.md`
- Runtime API 参考: `../api/runtime.md`
- 工具系统 API 参考: `../api/tools.md`
- Toolkit 实现参考: `../api/toolkits.md`
- Memory API 参考: `../api/memory.md`
- Input、Workspace 与 Schema 参考: `../api/input-workspace-schemas.md`
