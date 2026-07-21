# Unchain native host protocol v1

Status: experimental, compatibility-frozen at major version `1` once a Python client ships.

This protocol is the seam between the existing Python runtime and the incremental Rust kernel. It is deliberately local and transport-neutral: v1 uses a child process over stdin/stdout, so PuPu can supervise the same binary directly from Electron without opening a localhost port.

## Compatibility promise

- Python remains a complete, supported execution path throughout the migration. The native host is opt-in until parity, recovery, and rollback gates pass.
- A protocol major version changes only for incompatible wire changes. Minor versions may add optional fields, methods, capabilities, or error details.
- Client and host negotiate the highest shared minor version during `hello`/`ready`.
- Capability discovery, rather than host version checks, gates optional behavior.
- The v1 fixtures in `protocol/fixtures/v1/` are golden compatibility inputs for every implementation.

## Transport and framing

- One UTF-8 JSON object per line (JSONL).
- Client messages are written to host stdin; host messages are written to stdout.
- Host diagnostics go to stderr. Protocol data must never be written to stderr.
- A frame is limited to 1 MiB. Empty lines are ignored.
- End-of-file requests a clean shutdown. Process exit or malformed framing fails the session closed.
- v1 processes one request at a time and emits exactly one correlated `result` or `error` for each accepted request.

The default deployment is a private child process. The wire contract does not assume TCP and must not expose a listening socket without a separate authenticated transport design.

## Identifiers

`rpc_id` correlates a single wire exchange. `request_id` is stable across retries and will later support idempotency. Both are printable ASCII strings between 1 and 128 bytes.

## Handshake

The first non-empty frame must be `hello`:

```json
{"type":"hello","protocol":"unchain.host","rpc_id":"rpc-hello","client":{"name":"unchain-python","version":"0.2.0"},"supported_protocol":{"major":1,"min_minor":0,"max_minor":0}}
```

The host returns `ready`, including its identity, the selected version, ordered capabilities, and resource limits. Incompatible versions or an invalid protocol magic terminate the session after a correlated error.

## Requests and responses

Every post-handshake request carries the negotiated version:

```json
{"type":"request","protocol":"unchain.host","protocol_version":{"major":1,"minor":0},"rpc_id":"rpc-version","request_id":"req-version","method":"host.version","params":{}}
```

Supported foundation methods:

| Method | Purpose |
| --- | --- |
| `host.version` | Build, target, instance, and negotiated protocol metadata |
| `host.capabilities` | Ordered capability registry and current limits |
| `host.self_test` | Fast, machine-readable startup health checks |

Unknown methods and invalid parameters are recoverable request errors. Handshake, framing, I/O, and version failures terminate the session.

## Error shape

Errors preserve every available correlation field and use stable machine-readable codes:

```json
{"type":"error","protocol":"unchain.host","protocol_version":{"major":1,"minor":0},"rpc_id":"rpc-missing","request_id":"req-missing","method":"host.missing","error":{"code":"request.method_not_found","message":"unsupported method","retryable":false,"details":{}}}
```

Callers must branch on `error.code`, never `error.message`. A retry is permitted only when `retryable` is true and the method's idempotency contract allows it.
