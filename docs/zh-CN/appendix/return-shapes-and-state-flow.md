# 返回结构与状态流速查

| 表面 | 细节 |
| --- | --- |
| `Agent.run()` / `Broth.run()` bundle | 包含 `consumed_tokens`、`stop_reason`、`artifacts`，以及在暂停时附带的 `toolkit_catalog_token` 与 `continuation`。 |
| `Team.run()` result | 包含 `transcript`、`events`、`stop_reason`、`final_text`、`final_agent` 和按 agent 名称分组的 `agent_bundles`。 |
| Toolkit catalog continuation | 包含 catalog state token，使恢复后的运行能拿回同一套 managed/active toolkit runtime。 |
| Human input continuation | 携带请求元数据与会话状态，使 `resume_human_input()` 可以确定性继续执行。 |

## 状态流检查表

- 工具确认和人类输入都会返回 continuation。
- toolkit catalog runtime 会通过 state token 跨暂停/恢复保存。
- Team 运行会为每个 agent 派生 session 和 memory namespace。
- workspace pins 存放在 session store，而不是 toolkit 实例内。
