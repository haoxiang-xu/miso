# 测试约定

`testing-conventions` 主题的正式简体中文 skills 章节。

## 角色与边界

本章说明仓库如何在不依赖真实 provider 的前提下测试编排、工具执行、memory、registry 行为和 eval fixture。

## 依赖关系

- 单元测试主要 monkeypatch provider fetch 或使用 fake adapter。
- toolkit discovery 测试会生成临时 manifest package。
- notebook/eval fixture 与主单测树并列但相互隔离。

## 核心对象

- `ProviderTurnResult`
- `ToolCall`
- `MemoryManager` fake
- `ToolkitRegistry` fixture
- `EvalCase`/`JudgeReport` in `tests/evals/`

## 执行流与状态流

- patch provider fetch 来模拟 tool turn。
- 用 fake store/adapter 获得确定性的 memory 行为。
- 把 eval fixture 与单元测试分开运行。
- 通过临时 package 和 tmp path 验证新 toolkit manifest 与安全约束。

## 配置面

- Python 3.12 virtualenv 与 editable install。
- `pyproject.toml` 中的 pytest 发现配置。
- 通过 `pytest -k` 或单文件运行做定点调试。

## 扩展点

- 优先新增 fake adapter，而不是调用真实服务。
- 在 `tests/evals/` 下增加端到端行为用例。
- 保持测试 helper 命名与 fixture 布局一致。

## 常见陷阱

- `tests/evals/fixtures` 被有意排除在 pytest discovery 之外。
- 多轮 provider 行为经常用 state dict 建模。
- 验证事件顺序时，callback 断言往往是唯一可靠手段。

## 关联 class 参考

- [Runtime API](../api/runtime.md)
- [Memory API](../api/memory.md)
- [Tool System API](../api/tools.md)

## 源码入口

- `tests/`
- `tests/evals/`
- `pyproject.toml`

## 详细说明

本章与英文版保持相同的阅读顺序，但把重点放在结构、调用链和对象边界上；API 级细节请与相邻的参考页配套阅读。

- Agents API 参考: `../api/agents.md`
- Runtime API 参考: `../api/runtime.md`
- 工具系统 API 参考: `../api/tools.md`
- Toolkit 实现参考: `../api/toolkits.md`
- Memory API 参考: `../api/memory.md`
- Input、Workspace 与 Schema 参考: `../api/input-workspace-schemas.md`
- Pupu 子系统参考: `../api/pupu.md`
