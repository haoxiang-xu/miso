# Glossary

| Term | Meaning |
| --- | --- |
| `Agent` | High-level single-agent facade that configures tools, memory, and runtime options. |
| `KernelLoop` | Harness-driven execution loop that performs provider turns and tool loops. |
| `HarnessDelta` | Immutable state mutation returned by a harness (append/insert/replace/delete messages). |
| `RunState` | Mutable run state that tracks messages, token usage, and provider state. |
| `Toolkit` | A collection of tools executable by name. |
| `Toolkit Catalog` | Runtime layer that can activate or deactivate managed toolkits during a run. |
| `Workspace Pin` | Session-scoped pinned file context injected back into later prompts. |
| `Continuation` | Serialized state returned when a run pauses for confirmation or human input. |
