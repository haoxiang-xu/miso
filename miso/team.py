from __future__ import annotations

import copy
import uuid
from typing import Any, Callable

from ._agent_shared import as_text, normalize_mentions
from .agent import Agent


class Team:
    def __init__(
        self,
        *,
        agents: list[Agent],
        owner: str,
        channels: dict[str, list[str]],
        mode: str = "channel_collab",
        visible_transcript: bool = True,
        completion_policy: str = "owner_finalize",
        max_steps: int = 24,
    ):
        if not agents:
            raise ValueError("Team requires at least one Agent")

        ordered_agents: list[Agent] = []
        seen_names: set[str] = set()
        for agent in agents:
            if not isinstance(agent, Agent):
                raise TypeError("Team agents must all be Agent instances")
            if agent.name in seen_names:
                raise ValueError(f"duplicate Agent name in Team: {agent.name}")
            seen_names.add(agent.name)
            ordered_agents.append(agent)

        if owner not in seen_names:
            raise ValueError(f"Team owner '{owner}' is not present in agents")
        if not isinstance(channels, dict) or not channels:
            raise ValueError("Team channels are required")

        normalized_channels: dict[str, list[str]] = {}
        for channel_name, subscribers in channels.items():
            if not isinstance(channel_name, str) or not channel_name.strip():
                raise ValueError("channel names must be non-empty strings")
            if not isinstance(subscribers, list) or not subscribers:
                raise ValueError(f"channel '{channel_name}' must list at least one subscriber")
            normalized = []
            for subscriber in subscribers:
                if subscriber not in seen_names:
                    raise ValueError(f"channel '{channel_name}' references unknown agent '{subscriber}'")
                normalized.append(subscriber)
            normalized_channels[channel_name.strip()] = normalized

        self._agent_order = [agent.name for agent in ordered_agents]
        self.agents = {agent.name: agent for agent in ordered_agents}
        self.owner = owner
        self.channels = normalized_channels
        self.mode = mode
        self.visible_transcript = bool(visible_transcript)
        self.completion_policy = completion_policy
        self.max_steps = max(1, int(max_steps))

    def _make_envelope(
        self,
        *,
        sender: str,
        content: str,
        step: int,
        channel: str | None = None,
        kind: str = "message",
        mentions: list[str] | None = None,
        target: str | None = None,
    ) -> dict[str, Any]:
        return {
            "id": str(uuid.uuid4()),
            "channel": channel or "",
            "sender": sender,
            "content": content,
            "kind": kind,
            "mentions": normalize_mentions(mentions, content=content),
            "target": target or "",
            "step": step,
        }

    def _coerce_user_content(self, messages: str | list[dict[str, Any]]) -> str:
        if isinstance(messages, str):
            return messages
        if isinstance(messages, list):
            lines: list[str] = []
            for item in messages:
                if not isinstance(item, dict):
                    continue
                role = str(item.get("role", "user"))
                content = as_text(item.get("content", "")).strip()
                if content:
                    lines.append(f"{role}: {content}")
            return "\n".join(lines)
        raise TypeError("Team.run input must be a string or a list of messages")

    def _select_next_agent(self, pending: dict[str, list[dict[str, Any]]]) -> str | None:
        best_name: str | None = None
        best_score: tuple[int, int, int, int] | None = None

        for order, agent_name in enumerate(self._agent_order):
            inbox = pending.get(agent_name, [])
            if not inbox:
                continue
            has_handoff = any(
                item.get("kind") == "handoff" and item.get("target") == agent_name
                for item in inbox
            )
            has_mention = any(agent_name in item.get("mentions", []) for item in inbox)
            has_user = any(item.get("sender") == "user" for item in inbox)
            owner_bonus = 1 if agent_name == self.owner else 0
            score = (
                3 if has_handoff else 0,
                2 if has_mention else 0,
                owner_bonus if has_user else 0,
                -order,
            )
            if best_score is None or score > best_score:
                best_name = agent_name
                best_score = score

        return best_name

    def _deliver_channel_message(
        self,
        envelope: dict[str, Any],
        pending: dict[str, list[dict[str, Any]]],
    ) -> None:
        channel = envelope.get("channel", "")
        subscribers = self.channels.get(channel, [])
        for agent_name in subscribers:
            if agent_name == envelope.get("sender"):
                continue
            pending.setdefault(agent_name, []).append(copy.deepcopy(envelope))

    def run(
        self,
        messages: str | list[dict[str, Any]],
        *,
        entry_channel: str | None = None,
        payload: dict[str, Any] | None = None,
        callback: Callable[[dict[str, Any]], None] | None = None,
        session_id: str | None = None,
        memory_namespace: str | None = None,
        max_steps: int | None = None,
    ) -> dict[str, Any]:
        step_limit = max(1, int(max_steps or self.max_steps))
        run_id = str(uuid.uuid4())
        transcript: list[dict[str, Any]] = []
        events: list[dict[str, Any]] = []
        pending: dict[str, list[dict[str, Any]]] = {name: [] for name in self.agents}

        selected_entry_channel = entry_channel or ("shared" if "shared" in self.channels else next(iter(self.channels)))
        if selected_entry_channel not in self.channels:
            raise ValueError(f"unknown Team entry channel: {selected_entry_channel}")

        initial_envelope = self._make_envelope(
            sender="user",
            channel=selected_entry_channel,
            content=self._coerce_user_content(messages),
            step=0,
            kind="task",
            mentions=[self.owner],
        )
        transcript.append(initial_envelope)
        self._deliver_channel_message(initial_envelope, pending)
        events.append({
            "type": "message_published",
            "agent": "user",
            "channel": selected_entry_channel,
            "step": 0,
            "content": initial_envelope["content"],
            "run_id": run_id,
        })

        final_text = ""
        final_agent = ""
        stop_reason = "quiescent"

        for step_index in range(1, step_limit + 1):
            next_agent = self._select_next_agent(pending)
            if next_agent is None:
                stop_reason = "quiescent"
                break

            inbox = copy.deepcopy(pending.get(next_agent, []))
            pending[next_agent] = []
            events.append({
                "type": "scheduled",
                "agent": next_agent,
                "step": step_index,
                "inbox_size": len(inbox),
                "run_id": run_id,
            })
            if callback is not None:
                callback(copy.deepcopy(events[-1]))

            scoped_session_id = f"{session_id}:{next_agent}" if session_id else f"{run_id}:{next_agent}"
            scoped_namespace = f"{memory_namespace}:{next_agent}" if memory_namespace else scoped_session_id

            result = self.agents[next_agent].step(
                inbox=inbox,
                channels=self.channels,
                owner=self.owner,
                team_transcript=transcript,
                mode=self.mode,
                payload=payload,
                callback=callback,
                session_id=scoped_session_id,
                memory_namespace=scoped_namespace,
            )

            published_any = False
            for item in result.get("publish", []):
                if not isinstance(item, dict):
                    continue
                channel = str(item.get("channel", "")).strip()
                content = str(item.get("content", "")).strip()
                if channel not in self.channels or not content:
                    events.append({
                        "type": "invalid_publish",
                        "agent": next_agent,
                        "channel": channel,
                        "step": step_index,
                        "run_id": run_id,
                    })
                    continue
                envelope = self._make_envelope(
                    sender=next_agent,
                    channel=channel,
                    content=content,
                    kind=str(item.get("kind", "message") or "message"),
                    mentions=item.get("mentions") if isinstance(item.get("mentions"), list) else [],
                    step=step_index,
                )
                transcript.append(envelope)
                self._deliver_channel_message(envelope, pending)
                published_any = True
                event = {
                    "type": "message_published",
                    "agent": next_agent,
                    "channel": channel,
                    "step": step_index,
                    "content": content,
                    "mentions": copy.deepcopy(envelope["mentions"]),
                    "run_id": run_id,
                }
                events.append(event)
                if callback is not None:
                    callback(copy.deepcopy(event))

            handoff = result.get("handoff")
            if isinstance(handoff, dict):
                target = str(handoff.get("agent", "")).strip()
                content = str(handoff.get("content", "")).strip()
                if target in self.agents:
                    envelope = self._make_envelope(
                        sender=next_agent,
                        target=target,
                        content=content or f"Handoff from {next_agent}",
                        kind="handoff",
                        step=step_index,
                    )
                    transcript.append(envelope)
                    pending.setdefault(target, []).append(copy.deepcopy(envelope))
                    published_any = True
                    event = {
                        "type": "handoff",
                        "agent": next_agent,
                        "target": target,
                        "step": step_index,
                        "content": envelope["content"],
                        "run_id": run_id,
                    }
                    events.append(event)
                    if callback is not None:
                        callback(copy.deepcopy(event))

            final_candidate = str(result.get("final", "")).strip()
            if final_candidate:
                if self.completion_policy == "owner_finalize" and next_agent != self.owner:
                    events.append({
                        "type": "final_ignored",
                        "agent": next_agent,
                        "step": step_index,
                        "run_id": run_id,
                    })
                else:
                    final_text = final_candidate
                    final_agent = next_agent
                    stop_reason = "owner_finalized" if next_agent == self.owner else "finalized"
                    event = {
                        "type": "finalized",
                        "agent": next_agent,
                        "step": step_index,
                        "content": final_text,
                        "run_id": run_id,
                    }
                    events.append(event)
                    if callback is not None:
                        callback(copy.deepcopy(event))
                    break

            if not published_any and not final_candidate and bool(result.get("idle")):
                events.append({
                    "type": "idle",
                    "agent": next_agent,
                    "step": step_index,
                    "run_id": run_id,
                })
                if callback is not None:
                    callback(copy.deepcopy(events[-1]))
        else:
            stop_reason = "max_steps"

        return {
            "run_id": run_id,
            "final": final_text,
            "final_agent": final_agent,
            "owner": self.owner,
            "steps": len([event for event in events if event.get("type") == "scheduled"]),
            "stop_reason": stop_reason,
            "events": events,
            "transcript": transcript if self.visible_transcript else [],
        }


__all__ = ["Team"]
