# Sync Unchain and Miso Packages

Ensure changes made in `src/unchain/` are properly mirrored to `src/miso/` (or vice versa) where the two packages share parallel implementations.

## Arguments
- $ARGUMENTS: Optional specific module to sync (e.g. "tools", "toolkits", "memory"). If empty, check all shared modules.

## Steps

1. The two packages share parallel implementations in these areas:
   - `tools/` — Tool, Toolkit, decorators, models, registry, catalog
   - `toolkits/builtin/` — WorkspaceToolkit, TerminalToolkit, ExternalAPIToolkit, AskUserToolkit
   - `memory/` — MemoryManager, MemoryConfig, strategies, stores, Qdrant adapter
   - `schemas/` — ResponseFormat
   - Runtime resources — `model_capabilities.json`, `model_default_payloads.json`

2. For each shared module (or the specific one from $ARGUMENTS):
   - Diff the unchain and miso versions
   - Identify meaningful divergences (not just import paths)
   - Report which files are out of sync

3. For each divergence, determine:
   - Is unchain the source of truth? (usually yes for new code)
   - Is miso's version intentionally different? (legacy compat)
   - Should they be synced?

4. Apply changes if appropriate, preserving import path differences
5. Run tests to verify: `PYTHONPATH=src pytest tests/ -q --tb=short`
