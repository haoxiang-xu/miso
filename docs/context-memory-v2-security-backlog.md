# Context Memory V2 Security Backlog

This document keeps non-blocking hardening work from expanding the Memory V2
P0 delivery scope. New findings belong here unless they meet one of the P0
stop-ship criteria below.

## Permanent trust-boundary decision

Unchain does **not** provide capability isolation between objects or code
running inside the same Python process. The supported security boundaries are:

- process and IPC/network boundaries;
- durable disk and database boundaries; and
- explicitly declared external secret-resolution sinks.

Python code already executing inside the Unchain process is part of the same
trusted computing base. Access to private attributes, module globals, closure
cells, monkeypatching, `object.__setattr__`, or replacement of issuer internals
is therefore not an authorization bypass under the Unchain threat model.

In-process tamper resistance may be tested as ordinary robustness, but it must
not produce a security rejection, block P0, or start another capability-
hardening cycle. This decision is permanent unless the product explicitly
introduces a new process-isolation boundary.

## P0 stop-ship criteria

A finding may interrupt the locked implementation sequence only when it can:

1. make a required Memory V2 function impossible to implement correctly;
2. corrupt or irreversibly lose durable user data;
3. expose plaintext secrets outside the approved late-resolution sink; or
4. repeat a tool or provider operation with externally visible side effects.

All other hardening, defense-in-depth, and hostile in-process mutation cases
are documented here and scheduled after the P0 production path is complete.

## Deferred findings

### CMV2-SEC-001: Hostile custom `KernelLoop` descriptor semantics

- **Status:** Closed — outside the supported security boundary
- **Scope:** Kernel final model/tool boundary
- **Finding:** A deliberately hostile custom data descriptor could write a
  boundary seal and then raise from `object.__setattr__`, producing semantics
  that the ordinary exact `KernelLoop` class does not have.
- **Current boundary:** Production Memory V2 uses the exact standard
  `KernelLoop`; custom subclasses and class-level monkeypatching are outside
  the current in-process authority threat model.
- **Disposition:** No P0 security work. Exact-type admission may be added later
  only if required for functional correctness.

### CMV2-SEC-002: Python in-process issuer TCB isolation

- **Status:** Closed — not a product security boundary
- **Scope:** Prepared provider turn authorities
- **Finding:** Python does not provide a cryptographic privacy boundary against
  code that is allowed arbitrary closure-cell or module-global mutation.
- **Current boundary:** Draft, seal, and anchor construction is issuer-owned;
  public prepared objects expose no live tool authority; arbitrary mutation of
  issuer closure cells or replacement of the issuer itself is outside the P0
  capability threat model.
- **Disposition:** No further issuer/capability hardening inside the Python
  process. A future requirement must first introduce an explicit process or
  native isolation boundary and a separate threat model.

## Locked implementation sequence

1. `ProviderWireEnvelope`
2. repository-issued durable catalog/wire authority
3. durable provider retry lease/CAS
4. OpenAI, Anthropic, Hyperspace, and Ollama integration
5. ContextRuntime admission and restart recovery
6. Artifact/Handoff and Curator/Toolkit host adapters
7. Memory Agent, long-term promotion, Vault, UI/Trace, migration, and rollout
