"""Model-visible policy for proposing durable Memory V2 candidates."""

from __future__ import annotations

from unchain.tools import ToolPromptSpec


MEMORY_PROPOSAL_POLICY_VERSION = "unchain.memory.proposal_policy.v1"

MEMORY_PROPOSE_PROMPT_SPEC = ToolPromptSpec(
    purpose=(
        "Propose one auditable chat-memory candidate for future work; this never "
        "writes formal or long-term memory. Policy version: "
        f"{MEMORY_PROPOSAL_POLICY_VERSION}. Except for an explicit user request to "
        "remember, preserve, correct, or replace information, call this tool only "
        "when every Evidence, Future value, Durability, and Novelty test below passes."
    ),
    when_to_use=(
        (
            "Explicit intent — Handle a user's explicit request to remember, preserve, "
            "correct, or replace durable information, subject to secret and duplicate "
            "checks. When memory_search is available, search first; if equivalent "
            "memory already exists, do not create a duplicate."
        ),
        (
            "Evidence — The candidate comes from a direct user statement, a confirmed "
            "decision, or a verified tool or test result, not a guess or an unaccepted "
            "assistant proposal."
        ),
        (
            "Future value — After current context is gone, losing the information would "
            "likely cause a materially worse future action or force the user to repeat "
            "important information."
        ),
        (
            "Durability — The information remains useful beyond the current exchange. "
            "High-signal examples are stable user preferences and standing collaboration "
            "rules; confirmed project decisions, invariants, conventions, and constraints; "
            "verified reusable procedures and failure shields; durable environment facts "
            "that are costly to rediscover; and references to important durable artifacts."
        ),
        (
            "Novelty — The candidate adds, corrects, or materially contextualizes existing "
            "knowledge instead of paraphrasing it. For a likely correction, search when "
            "possible, use the intended target path, and explain what changed in rationale; "
            "never silently overwrite a conflict."
        ),
    ),
    when_not_to_use=(
        (
            "Never store passwords, tokens, credentials, private keys, secret values, or "
            "provider credentials. Secrets belong only in the Secret Vault."
        ),
        (
            "Do not store guesses, inferred personal traits, unaccepted assistant ideas, "
            "or lessons that have not been confirmed by the user or verified by execution."
        ),
        (
            "Do not store greetings, acknowledgements, small talk, one-off requests, "
            "temporary state, tentative ideas, rejected options, or common public knowledge."
        ),
        (
            "Do not store current objectives, progress, active plans, or open questions; "
            "those belong in Pinned Task State. Do not copy raw transcripts, logs, hidden "
            "reasoning, or full tool or subagent outputs into memory."
        ),
        (
            "Do not proactively propose highly sensitive personal information without the "
            "user's explicit request, and never infer sensitive attributes as facts."
        ),
    ),
    examples=(
        (
            "A user establishes a standing project rule or confirms an architectural "
            "invariant: propose the durable rule with its conditions and source references."
        ),
        (
            "A tested workaround reliably prevents a recurring failure: propose the "
            "verified failure shield, not the raw debug transcript."
        ),
        (
            "A user requests CSV formatting only for the current answer: do not propose it."
        ),
        (
            "A tool returns a large log or document: preserve it as an ArtifactRef and "
            "propose only a durable, verified takeaway or index when useful."
        ),
    ),
    advanced_tips=(
        (
            "Create one coherent, standalone candidate. Preserve exact names, dates, "
            "quantities, conditions, uncertainty, meaningful path and description, and "
            "source references when available."
        ),
        (
            "A normal proposal is chat-scoped. Long-term memory requires a separate, "
            "user-confirmed promotion."
        ),
        (
            "After memory_propose succeeds, say only that a memory candidate was proposed. "
            "Claim formal memory was saved only when a curator or formal-write result "
            "confirms it. If nothing passes this policy, do not call the tool, and never "
            "interrupt the primary task merely to manufacture a memory."
        ),
    ),
)


__all__ = [
    "MEMORY_PROPOSAL_POLICY_VERSION",
    "MEMORY_PROPOSE_PROMPT_SPEC",
]
