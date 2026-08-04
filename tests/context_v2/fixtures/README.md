# PuPu context-v2 golden fixtures

These synthetic, provider-neutral fixtures freeze the PuPu P0 context-memory
contract for Unchain's context compiler. They contain no production prompts,
user data, timestamps, or provider wire formats.

Regenerate them from the PuPu repository root with the target Unchain
checkout's virtual-environment Python:

```sh
/path/to/unchain/.venv/bin/python \
  unchain_runtime/server/tests/export_memory_v2_contract_fixtures.py \
  /path/to/unchain/tests/context_v2/fixtures/pupu_p0
```

That interpreter must provide the target Unchain checkout's `unchain`
package. The exporter adds only PuPu's local server directory to its import
path and does not guess where another checkout lives.

The exporter is deterministic. Its manifest records the PuPu revision, the
intentional dirty-tree provenance, a digest of an explicit source allowlist,
and a SHA-256 digest for each fixture.
