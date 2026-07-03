# Core Toolkit

`CoreToolkit` is the compatibility bundle for the focused builtin toolkits. It keeps the legacy core surface for precise file reads, guarded writes, string edits, globbing, grep-style search, public web fetch, cross-platform shell execution, LSP-powered code intelligence, and structured user questions.

New code should prefer the focused surfaces:

- `WorkspaceToolkit` for workspace and coding tools.
- `InteractionToolkit` for structured user questions.
- `WebToolkit` for public web fetch and extraction.

## What this README is for

- Quick package-local orientation
- Stable manifest-facing README path
- Pointer to the full generated docs set

## Full documentation

- English API reference: [CoreToolkit](../../../../../docs/en/api/toolkits.md#coretoolkit)
- 中文 API 参考: [CoreToolkit](../../../../../docs/zh-CN/api/toolkits.md#coretoolkit)
- English docs index: [docs/README.en.md](../../../../../docs/README.en.md)
- 中文文档索引: [docs/README.zh-CN.md](../../../../../docs/README.zh-CN.md)

## Notes

This file stays intentionally short. `CoreToolkit` remains available as a compatibility bundle, while broader runtime and toolkit documentation continues to live under `docs/`.
