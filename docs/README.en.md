# Unchain Encyclopedia

`unchain` now ships a two-layer documentation set: concise repository entry points and a full bilingual encyclopedia covering skills chapters and every production class under `src/unchain`.

Language switch: [English](README.en.md) | [简体中文](README.zh-CN.md)

## Reading paths

- Start with the skills chapters for architecture and execution flow, then move into the API reference for specific classes.
- Use the appendices when you need exhaustive coverage checks, exported-symbol lookup, or return-shape summaries.
- Builtin toolkit package READMEs stay short on purpose; treat them as package entry hints, not the canonical reference.

## Skills chapters

- [Architecture Overview](en/skills/architecture-overview.md)
- [Agent and Team](en/skills/agent-and-team.md)
- [Runtime Engine](en/skills/runtime-engine.md)
- [Tool System Patterns](en/skills/tool-system-patterns.md)
- [Memory System](en/skills/memory-system.md)
- [Creating Builtin Toolkits](en/skills/creating-builtin-toolkits.md)
- [Testing Conventions](en/skills/testing-conventions.md)

## API reference

- [Agents API Reference](en/api/agents.md)
- [Runtime API Reference](en/api/runtime.md)
- [Tool System API Reference](en/api/tools.md)
- [Toolkit Implementations Reference](en/api/toolkits.md)
- [Memory API Reference](en/api/memory.md)
- [Input, Workspace, and Schema Reference](en/api/input-workspace-schemas.md)

## Appendices

- [Class Index](en/appendix/class-index.md)
- [Export Index](en/appendix/export-index.md)
- [Glossary](en/appendix/glossary.md)
- [Return Shapes and State Flow](en/appendix/return-shapes-and-state-flow.md)

## Coverage commitments

- All 55 production classes under `src/unchain` are indexed exactly once.
- Public exports from package `__init__` files are cross-linked into the reference tree.
- English and Chinese docs share the same chapter and page layout.
