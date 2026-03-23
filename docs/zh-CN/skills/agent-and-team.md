# Agent 与 Team

`agent-and-team` 主题的正式简体中文 skills 章节。

## 角色与边界

本章记录高层编排表面：单 Agent 如何配置和运行、Team 如何协作，以及子代理如何复用同一执行模型。

## 依赖关系

- `Agent` 负责工具归一化、memory 强制转换和 `Broth` 构造。
- `Team` 每一步都委托给具名 `Agent`，自身只管理共享路由和打分状态。
- `ResponseFormat`、toolkit catalog 状态和 memory namespace 会贯穿 run/resume 表面。

## 核心对象

- `Agent`
- `Team`
- `_SubagentConfig`
- `_SubagentCounters`
- `_SubagentRuntime`

## 执行流与状态流

- `Agent.run()` 合并工具、创建新的 runtime，并执行到完成或暂停。
- `Agent.resume_human_input()` 恢复挂起的 catalog/runtime 状态并继续同一会话。
- `Team.run()` 发布初始 envelope、给待处理工作打分，然后让 agent 发布/handoff/finalize，直到静默或完成。

## 配置面

- Agent identity/instructions/provider/model。
- 短期和长期 memory 配置。
- 子代理限制、toolkit catalog 设置以及 `max_iterations` 之类的每次运行覆盖项。

## 扩展点

- 通过 `Agent.as_tool()` 把 agent 暴露为工具。
- 通过 `enable_subagents()` 开启嵌套代理委托。
- 用 callback 从 solo/team 运行中流式接收事件。

## 常见陷阱

- `Team` 会强制 agent 名称唯一并要求 owner 有效。
- 子代理深度与子数量计数用于防止失控递归。
- 暂停后的恢复必须使用上一轮返回的 continuation。

## 关联 class 参考

- [Agents API](../api/agents.md)
- [Runtime API](../api/runtime.md)
- [Memory API](../api/memory.md)

## 源码入口

- `src/miso/agents/agent.py`
- `src/miso/agents/team.py`

## Agent 是什么

`Agent` 是面向调用方的高层接口。它负责保存 agent 的身份、默认指令、工具集合、memory 配置以及每次运行的默认覆盖项；但它自己并不直接执行 provider loop，而是在每次 `run()` 时创建新的 `Broth` 去完成底层执行。

换句话说，`Agent` 更像“配置容器 + 入口表面”，而 `Broth` 更像“单次运行的执行器”。前者决定这个 agent 是谁、带着什么默认能力；后者负责把这一轮请求真正跑完。

## 当前实现里的运行链路

1. `Agent.run()` 会先把字符串或消息列表规范化，再把 `instructions` 和额外 system 消息拼进 conversation 顶部。
2. 它会合并默认 payload、response format 和运行时覆盖项，并解析当前是否要启用子代理运行时。
3. `_build_engine()` 会创建一个新的 `Broth`，把 provider、model、api key、memory manager、toolkit catalog 配置和 agent 的工具集合全部挂进去。
4. 真正执行时，`Agent` 会把已经组装好的 messages 和参数转发给 `engine.run()`；如果运行因为人类输入而暂停，`Agent.resume_human_input()` 会重新创建 runtime，再把 continuation 状态恢复进去继续执行。
5. 对于启用了 toolkit catalog 的运行，`Agent` 还会在暂停前后捕获和恢复 catalog state token，确保恢复后的 runtime 看到的是同一组 active/managed toolkits。

## 设计取舍

这种分层让 `Agent` 保持为稳定的高层 API，而把 provider 适配、tool loop、暂停恢复和 token 统计集中到 `Broth`。这样做的直接收益是：

- 每次运行都使用 fresh runtime，不容易残留隐式状态。
- memory、toolkit catalog 和 continuation 都能以显式参数或状态对象的形式流转，而不是藏在长生命周期实例里。
- 高层调用方通常只需要理解 `Agent.run()` / `Agent.resume_human_input()`，不需要直接处理 provider 细节。

## 详细说明

本章与英文版保持相同的阅读顺序，但把重点放在结构、调用链和对象边界上；API 级细节请与相邻的参考页配套阅读。

- Agents API 参考: `../api/agents.md`
- Runtime API 参考: `../api/runtime.md`
- 工具系统 API 参考: `../api/tools.md`
- Toolkit 实现参考: `../api/toolkits.md`
- Memory API 参考: `../api/memory.md`
- Input、Workspace 与 Schema 参考: `../api/input-workspace-schemas.md`
