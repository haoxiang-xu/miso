# Miso 百科文档

`miso` 现在采用双层文档体系：仓库入口页保持精简，完整的中英双语百科正文覆盖 skills 章节和 `src/miso` 下全部生产类。

Language switch: [English](README.en.md) | [简体中文](README.zh-CN.md)

## 阅读路径

- 先读 skills 章节建立架构和执行流，再进入 API 参考定位具体类。
- 当你需要全量覆盖检查、导出符号检索或返回结构速查时，使用附录。
- builtin toolkit 包内 README 故意保持简短，应把它们视为入口提示，而不是正式参考。

## Skills 章节

- [架构总览](zh-CN/skills/architecture-overview.md)
- [Agent 与 Team](zh-CN/skills/agent-and-team.md)
- [Runtime Engine](zh-CN/skills/runtime-engine.md)
- [工具系统模式](zh-CN/skills/tool-system-patterns.md)
- [Memory 系统](zh-CN/skills/memory-system.md)
- [创建内置 Toolkit](zh-CN/skills/creating-builtin-toolkits.md)
- [测试约定](zh-CN/skills/testing-conventions.md)

## API 参考

- [Agents API 参考](zh-CN/api/agents.md)
- [Runtime API 参考](zh-CN/api/runtime.md)
- [工具系统 API 参考](zh-CN/api/tools.md)
- [Toolkit 实现参考](zh-CN/api/toolkits.md)
- [Memory API 参考](zh-CN/api/memory.md)
- [Input、Workspace 与 Schema 参考](zh-CN/api/input-workspace-schemas.md)

## 附录

- [类索引](zh-CN/appendix/class-index.md)
- [导出索引](zh-CN/appendix/export-index.md)
- [术语表](zh-CN/appendix/glossary.md)
- [返回结构与状态流速查](zh-CN/appendix/return-shapes-and-state-flow.md)

## 覆盖承诺

- `src/miso` 下全部 55 个生产类都会被准确索引一次。
- 各包 `__init__` 中的公开导出会被交叉链接到参考树。
- 中英文文档保持相同的章节和页面布局。
