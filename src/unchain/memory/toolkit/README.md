# Memory V2 System Toolkit

This package owns Unchain's model-visible Memory V2 tools. It is a system
toolkit, not a public recipe toolkit, so it deliberately has no discoverable
`toolkit.toml` manifest.

The host constructs one immutable run binding and one explicit role bundle:

- normal agent
- generic memory curator
- consolidation curator
- task-state curator

Scope identifiers never appear in model-callable signatures. External URI
strings are decoded by the host-bound reference codec into `ResourceRef`
values before any structured capability is invoked. Mutations receive a
deterministic operation id tied to the run binding and normalized payload.
Workspace and promotion adapters additionally require non-empty, unique,
bare journal-event provenance bound by the host for the current run. This
provenance has no compatibility default and is never selected by the model.

Promotion and conflict review tools only create proposals. Applying a
long-term promotion or a conflicting workspace revision remains a separate,
user-confirmed host action.
