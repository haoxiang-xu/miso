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
- 提交 memory，并返回消息和包含 stop reason、tokens、artifacts、continuation 状态的 bundle。

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

## 详细说明

本章与英文版保持相同的阅读顺序，但把重点放在结构、调用链和对象边界上；API 级细节请与相邻的参考页配套阅读。

- Agents API 参考: `../api/agents.md`
- Runtime API 参考: `../api/runtime.md`
- 工具系统 API 参考: `../api/tools.md`
- Toolkit 实现参考: `../api/toolkits.md`
- Memory API 参考: `../api/memory.md`
- Input、Workspace 与 Schema 参考: `../api/input-workspace-schemas.md`
- Pupu 子系统参考: `../api/pupu.md`
