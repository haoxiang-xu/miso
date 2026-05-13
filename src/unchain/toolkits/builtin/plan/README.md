# Plan Toolkit

The Plan toolkit stores structured implementation plans in process memory.

Use it to create a draft with `plan_start`, revise sections with `plan_update`, inspect the rendered Markdown with `plan_read`, and finalize the plan with `plan_finalize`. Finalization is confirmation-gated and returns both Markdown and a Codex-compatible `<proposed_plan>` block.

Plan state is tied to the current toolkit instance and is not persisted to disk. For interactive planning, pair this toolkit with `CoreToolkit` so agents can use `ask_user_question` for decisions that should come from the user.
