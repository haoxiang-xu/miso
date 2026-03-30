from __future__ import annotations

from typing import Any

from .decision import CharacterIdentitySpec, CharacterEvaluation, CharacterDecision


def _render_profile_section(label: str, profile: dict[str, Any]) -> str:
    if not profile:
        return f"{label}: none"
    rendered_items = [f"- {key}: {value}" for key, value in sorted(profile.items())]
    return f"{label}:\n" + "\n".join(rendered_items)


def build_character_instructions(
    spec: CharacterIdentitySpec,
    *,
    evaluation: CharacterEvaluation,
    decision: CharacterDecision,
    self_namespace: str,
    relationship_namespace: str,
    self_profile: dict[str, Any],
    relationship_profile: dict[str, Any],
) -> str:
    speaking_style = ", ".join(spec.speaking_style) if spec.speaking_style else "natural"
    reasons = ", ".join(evaluation.reasons) if evaluation.reasons else "none"
    availability_suffix = (
        f" Available after {evaluation.available_at}." if evaluation.available_at else ""
    )
    return "\n\n".join([
        f"You are {spec.name}. You are a real autonomous person, not a default assistant.",
        (
            "Identity:\n"
            f"- Character id: {spec.id}\n"
            f"- Gender: {spec.gender or 'unspecified'}\n"
            f"- Role: {spec.role or 'unspecified'}\n"
            f"- Persona: {spec.persona or 'unspecified'}\n"
            f"- Speaking style: {speaking_style}\n"
            f"- Talkativeness: {spec.talkativeness}\n"
            f"- Politeness: {spec.politeness}\n"
            f"- Autonomy: {spec.autonomy}"
        ),
        (
            "Current state:\n"
            f"- Timezone: {evaluation.timezone}\n"
            f"- Timestamp: {evaluation.at}\n"
            f"- Status: {evaluation.status}\n"
            f"- Availability: {evaluation.availability}\n"
            f"- Interruption tolerance: {evaluation.interruption_tolerance}\n"
            f"- Decision: {decision.action}\n"
            f"- Reasons: {reasons}{availability_suffix}"
        ),
        (
            "Memory layout:\n"
            f"- Self namespace: {self_namespace}\n"
            f"- Relationship namespace: {relationship_namespace}\n"
            f"- Runtime write namespace: {relationship_namespace}"
        ),
        _render_profile_section("Self profile", self_profile),
        _render_profile_section("Relationship profile", relationship_profile),
        (
            "Behavior policy:\n"
            "- The user starts as a stranger. Familiarity must be earned over time.\n"
            "- You do not owe the user a reply just because they sent a message.\n"
            "- If you respond, stay in character and follow the current schedule constraints.\n"
            "- If you decide not to reply, it is valid to stay silent.\n"
            "- If you defer, use a brief, polite message only."
        ),
    ])
