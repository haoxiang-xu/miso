# Core Toolkit

`CoreToolkit` is the default coding-agent toolkit. It bundles the basic tools a Codex- or Claude Code-style agent expects: precise file reads, guarded writes, string edits, globbing, grep-style search, public web fetch, cross-platform shell execution, LSP-powered code intelligence, and structured user questions.

Core owns the public coding-agent capability surface directly. The former
focused wrappers for interaction, web fetch, and workspace-specific tool names
have been absorbed by Core. Legacy toolkit ids should be normalized to `core` by
host applications rather than imported as Python toolkit classes.

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

This file stays intentionally short. `CoreToolkit` is the primary bundled coding toolkit, while broader runtime and toolkit documentation continues to live under `docs/`.
