# Plan Toolkit

The Plan toolkit stores implementation plans only inside the configured workspace. Structured state is written to `plans/<plan_id>.json`; user-readable Markdown is written to `plans/<plan_id>.md`.

Use it to create a draft with `plan_start`, revise sections with `plan_update`, inspect the current status and workspace file location with `plan_read`, and finalize the plan with `plan_finalize`. Finalization is confirmation-gated.

Successful tool calls return only `ok`, `plan_id`, `status`, `revision`, and `workspace_file`. They do not return embedded plan state, Markdown, artifacts, or proposed-plan blocks. `session_store` and `session_id` constructor arguments remain accepted for compatibility but are not read or written. Without `workspace_root`, plan tools return an error because workspace storage is required.

For interactive planning, pair this toolkit with `CoreToolkit` so agents can use `ask_user_question` for decisions that should come from the user.
