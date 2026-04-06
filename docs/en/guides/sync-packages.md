# Sync Packages

This guide describes how to check and synchronize shared implementations between the `unchain` package namespaces. Use it after making changes to a shared module to ensure consistency.

## Prerequisites

- Understanding of the dual-package layout (see [Architecture Overview](../skills/architecture-overview.md))
- Knowledge of which modules are shared vs. independent

## Reference Files

| File / Directory | Role |
|-----------------|------|
| `src/unchain/tools/` | Tool classes, decorators, models, registry, catalog |
| `src/unchain/toolkits/builtin/` | Built-in toolkit implementations |
| `src/unchain/memory/` | Memory manager, config, strategies, stores |
| `src/unchain/schemas/` | Shared schemas (e.g., `ResponseFormat`) |
| `src/unchain/runtime/resources/model_capabilities.json` | Model registry |
| `src/unchain/runtime/resources/model_default_payloads.json` | Default payloads |

## Shared Modules

The following areas have parallel implementations that may need synchronization:

| Area | Contents |
|------|----------|
| `tools/` | `Tool`, `Toolkit`, decorators, models, registry, catalog |
| `toolkits/builtin/` | CoreToolkit, ExternalAPIToolkit |
| `memory/` | MemoryManager, MemoryConfig, strategies, stores, Qdrant adapter |
| `schemas/` | ResponseFormat and related schemas |
| Runtime resources | `model_capabilities.json`, `model_default_payloads.json` |

## Steps

1. **Identify the module to sync.** Choose a specific area (e.g., `tools`, `toolkits`, `memory`) or check all shared modules.

2. **Diff the implementations.** Compare the two versions of each shared file and identify meaningful divergences (ignore import path differences, which are expected).

3. **Determine sync direction.** For each divergence:
   - Is the `unchain` namespace the source of truth? (Usually yes for new code.)
   - Is the other version intentionally different for backward compatibility?
   - Should they be synced, or is the divergence deliberate?

4. **Apply changes** where appropriate, preserving import path differences.

5. **Run the test suite** to verify nothing broke.

## Testing

After syncing, run the full test suite:

```bash
PYTHONPATH=src pytest tests/ -q --tb=short
```

## Related

- [Architecture Overview](../skills/architecture-overview.md) -- package layout and dual-namespace design
- [Testing Conventions](../skills/testing-conventions.md) -- how to run and structure tests
