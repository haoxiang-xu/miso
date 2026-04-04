# Return Shapes and State Flow

| Surface | Details |
| --- | --- |
| `Agent.run()` result (`KernelRunResult`) | `messages`, `token_usage`, `stop_reason`, and optional `suspend_state` when the run pauses. |
| Toolkit catalog continuation | Contains a catalog state token that lets a resumed run restore the same managed/active toolkit runtime. |
| Human input continuation | Carries request metadata plus conversation state so `resume_human_input()` can continue deterministically. |

## State flow checklist

- Tool confirmation and human input both return a continuation payload.
- Toolkit catalog runtime is keyed by a state token across pause/resume.
- Workspace pins live in the session store, not inside toolkit instances.
