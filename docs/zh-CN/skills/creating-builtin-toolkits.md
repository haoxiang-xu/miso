# 创建内置 Toolkit

`creating-builtin-toolkits` 主题的正式简体中文 skills 章节。

## 角色与边界

本章是为 miso 新增或维护 builtin toolkit 的实现指南。

## 依赖关系

- builtin toolkit 建立在 `Toolkit` 或 `BuiltinToolkit` 上。
- manifest 由 `ToolkitRegistry` 校验。
- 运行时安全依赖正确的 workspace path 解析、manifest 元数据和 shutdown 行为。

## 核心对象

- `BuiltinToolkit`
- `Toolkit`
- `ToolkitRegistry`
- `WorkspaceToolkit`
- `TerminalToolkit`
- `ExternalAPIToolkit`
- `AskUserToolkit`

## 执行流与状态流

- 创建目录、实现、manifest 和 readme。
- 注册每个工具并确保 manifest/runtime 一致。
- 从包导出 toolkit 并验证 discovery。
- 保持包内 README 精简，并指向正式文档树。

## 配置面

- manifest 字段，如 `id`、`factory`、`readme`、`[[tools]]`。
- 每个 tool 的 `observe` 和 `requires_confirmation`。
- workspace root、icon 资源与 registry 发现路径。

## 扩展点

- 新增 builtin toolkit。
- 挂接 history optimizer 或自定义参数元数据。
- 当需要文件系统安全时使用 `BuiltinToolkit`。

## 常见陷阱

- 工具方法名和 manifest 条目必须精确一致。
- workspace 感知 toolkit 必须安全解析路径。
- factory 必须是可导入的零参 callable。

## 关联 class 参考

- [Toolkit Implementations](../api/toolkits.md)
- [Tool System API](../api/tools.md)

## 源码入口

- `src/miso/toolkits/base.py`
- `src/miso/toolkits/builtin/`
- `src/miso/tools/registry.py`

## 详细说明

本章与英文版保持相同的阅读顺序，但把重点放在结构、调用链和对象边界上；API 级细节请与相邻的参考页配套阅读。

- Agents API 参考: `../api/agents.md`
- Runtime API 参考: `../api/runtime.md`
- 工具系统 API 参考: `../api/tools.md`
- Toolkit 实现参考: `../api/toolkits.md`
- Memory API 参考: `../api/memory.md`
- Input、Workspace 与 Schema 参考: `../api/input-workspace-schemas.md`
