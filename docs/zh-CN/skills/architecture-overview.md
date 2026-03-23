# 架构总览

`architecture-overview` 主题的正式简体中文 skills 章节。

## 角色与边界

本章解释包的分层、哪些模块是基础层，以及请求和数据如何从用户代码进入运行时循环再返回调用方。

## 依赖关系

- `miso.tools` 是基础层，依赖最少。
- `miso.runtime` 建立在 tools、memory、workspace、input、schema 之上。
- `miso.agents` 负责编排 `Broth`、memory 和 toolkits，但不会反向污染底层依赖。

## 核心对象

- `Agent` 与 `Team` 作为编排入口。
- `Broth` 作为工具调用引擎。
- `Tool`、`Toolkit`、`ToolkitRegistry`、`ToolkitCatalogRuntime` 作为工具层。
- `MemoryManager` 及其 stores/strategies 作为上下文基础设施。

## 执行流与状态流

- 用户代码构造 `Agent` 或 `Broth`。
- 运行时规范化工具、准备 memory、注入 pinned context，并发起 provider 调用。
- 工具调用会被执行，或因为确认/人类输入而暂停。
- 会话状态、artifacts 和 memory 写入会在结束或暂停前提交。

## 配置面

- provider/model/api key。
- memory 配置与长期适配器。
- toolkit catalog 与受管 toolkit 列表。

## 扩展点

- 在 `runtime/providers/` 新增 provider。
- 通过 toolkit manifest 添加 builtin 或 plugin。
- 替换 memory store/adapter，而不改变高层编排 API。

## 常见陷阱

- 顶层 API 故意很小，细节主要下沉到子包。
- 每次 `Agent.run()` 都会创建新的 `Broth`。
- catalog 激活是运行时状态，不是 import 时注册。

## 关联 class 参考

- [Agents API](../api/agents.md)
- [Runtime API](../api/runtime.md)
- [Tool System API](../api/tools.md)
- [Memory API](../api/memory.md)

## 源码入口

- `src/miso/__init__.py`
- `src/miso/agents/`
- `src/miso/runtime/`
- `src/miso/tools/`

## 详细说明

本章与英文版保持相同的阅读顺序，但把重点放在结构、调用链和对象边界上；API 级细节请与相邻的参考页配套阅读。

- Agents API 参考: `../api/agents.md`
- Runtime API 参考: `../api/runtime.md`
- 工具系统 API 参考: `../api/tools.md`
- Toolkit 实现参考: `../api/toolkits.md`
- Memory API 参考: `../api/memory.md`
- Input、Workspace 与 Schema 参考: `../api/input-workspace-schemas.md`
- Pupu 子系统参考: `../api/pupu.md`
