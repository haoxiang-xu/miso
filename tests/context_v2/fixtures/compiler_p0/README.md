# Unchain compiler P0 freeze

This directory is an Unchain branch-local freeze of compiler-owned output. It
does not claim cross-repository equivalence with PuPu's dirty P0 baseline.

Each fixture freezes the canonical UTF-8 JSON bytes for the complete
`messages` and `diagnostics` pair. The cases cover below-pressure preservation,
over-pressure checkpoint admission, an OpenAI-origin historical tool exchange
projected as the same neutral envelope for OpenAI/Anthropic/Ollama, and schema-v4
`artifact.recorded` / `handoff.recorded` projection.

PuPu-owned URI allocation, receipts, legacy adaptation, and host fingerprints
remain in `../pupu_p0`. Promotion to an exact cross-repository golden requires
the immutable baseline owner's checkpoint; tests must not infer that claim from
this branch-local manifest.
