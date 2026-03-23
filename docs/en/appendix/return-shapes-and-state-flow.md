# Return Shapes and State Flow

| Surface | Details |
| --- | --- |
| `Agent.run()` / `Broth.run()` bundle | `consumed_tokens`, `stop_reason`, `artifacts`, optional `toolkit_catalog_token`, and optional `continuation` payload when the run pauses. |
| `Team.run()` result | `transcript`, `events`, `stop_reason`, `final_text`, `final_agent`, and `agent_bundles` keyed by agent name. |
| Toolkit catalog continuation | Contains a catalog state token that lets a resumed run restore the same managed/active toolkit runtime. |
| Human input continuation | Carries request metadata plus conversation state so `resume_human_input()` can continue deterministically. |

## State flow checklist

- Tool confirmation and human input both return a continuation payload.
- Toolkit catalog runtime is keyed by a state token across pause/resume.
- Team runs scope sessions and memory namespaces per agent.
- Workspace pins live in the session store, not inside toolkit instances.
