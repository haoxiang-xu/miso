# Plan Toolkit

The Plan toolkit stores structured implementation plans in session state or process memory. When constructed with `workspace_root`, it also mirrors each rendered plan to `plans/<plan_id>.md` inside that workspace.

Use it to create a draft with `plan_start`, revise sections with `plan_update`, inspect the rendered Markdown with `plan_read`, and finalize the plan with `plan_finalize`. Finalization is confirmation-gated and returns both Markdown and a Codex-compatible `<proposed_plan>` block.

The structured plan state remains the source of truth. The workspace Markdown file is a readable/editable project artifact for users and tools that browse the real workspace. For interactive planning, pair this toolkit with `CoreToolkit` so agents can use `ask_user_question` for decisions that should come from the user.
