# 工具系统模式

`tool-system-patterns` 主题的正式简体中文 skills 章节。

## 角色与边界

本章覆盖框架工具抽象，从原始 callable 推断，到 manifest 发现，再到运行时激活。

## 依赖关系

- `Tool` 与 `Toolkit` 是本地执行原语。
- `ToolkitRegistry` 读取 manifest 并把元数据与运行时对象做一致性校验。
- `ToolkitCatalogRuntime` 在发现结果之上叠加运行时激活/停用能力。

## 核心对象

- `Tool`
- `Toolkit`
- `ToolParameter`
- `ToolkitRegistry`
- `ToolkitCatalogRuntime`
- `ToolConfirmationRequest`
- `ToolConfirmationResponse`

## 执行流与状态流

- 把 callable 包装成 `Tool`。
- 把它注册进 `Toolkit`。
- 通过 manifest 从 builtin/local/plugin 三种来源发现 toolkit。
- 按需允许模型在运行时激活/停用 toolkit。

## 配置面

- `observe` 与 `requires_confirmation`。
- history payload optimizer。
- registry 的 local roots、enabled plugins 与 catalog managed IDs。

## 扩展点

- 创建 builtin toolkit。
- 通过 entry points 发布 plugin toolkit。
- 自定义历史压缩和工具元数据。

## 常见陷阱

- 工具名冲突会阻止同时激活。
- manifest 与运行时元数据必须一致。
- `@tool` 返回的是 `Tool`，不是原始函数。

## 关联 class 参考

- [Tool System API](../api/tools.md)
- [Toolkit Implementations](../api/toolkits.md)
- [Builtin Toolkit Guide](creating-builtin-toolkits.md)

## 源码入口

- `src/unchain/tools/tool.py`
- `src/unchain/tools/toolkit.py`
- `src/unchain/tools/registry.py`
- `src/unchain/tools/catalog.py`

## 详细说明

本章与英文版保持相同的阅读顺序，但把重点放在结构、调用链和对象边界上；API 级细节请与相邻的参考页配套阅读。

- Agents API 参考: `../api/agents.md`
- Runtime API 参考: `../api/runtime.md`
- 工具系统 API 参考: `../api/tools.md`
- Toolkit 实现参考: `../api/toolkits.md`
- Memory API 参考: `../api/memory.md`
- Input、Workspace 与 Schema 参考: `../api/input-workspace-schemas.md`
