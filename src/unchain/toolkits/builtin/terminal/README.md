# Terminal Toolkit

Terminal access for coding agents.

## Included tools

- `terminal_exec`
- `terminal_session_open`
- `terminal_session_write`
- `terminal_session_close`

## Design

- `terminal_exec` runs a real shell command string through an allowed shell in the workspace.
- Session tools provide persistent interactive shell access when one-shot commands are not enough.
- Strict mode blocks obviously dangerous commands before execution, but terminal remains the general escape hatch.
