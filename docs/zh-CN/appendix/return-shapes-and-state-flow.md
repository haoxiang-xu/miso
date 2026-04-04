# 返回结构与状态流速查

| 表面 | 细节 |
| --- | --- |
| `Agent.run()` 结果 (`KernelRunResult`) | 包含 `messages`、`token_usage`、`stop_reason`，以及在暂停时附带的 `suspend_state`。 |
| Toolkit catalog continuation | 包含 catalog state token，使恢复后的运行能拿回同一套 managed/active toolkit runtime。 |
| Human input continuation | 携带请求元数据与会话状态，使 `resume_human_input()` 可以确定性继续执行。 |

## 状态流检查表

- 工具确认和人类输入都会返回 continuation。
- toolkit catalog runtime 会通过 state token 跨暂停/恢复保存。
- workspace pins 存放在 session store，而不是 toolkit 实例内。
