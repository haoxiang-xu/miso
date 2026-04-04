# 术语表

| 术语 | 含义 |
| --- | --- |
| `Agent` | 高层单 Agent 门面，用来配置 tools、memory 和 runtime 选项。 |
| `KernelLoop` | 基于 harness 驱动的执行循环，负责 provider turn 与工具循环。 |
| `HarnessDelta` | harness 返回的不可变状态变更（追加/插入/替换/删除消息）。 |
| `RunState` | 可变运行状态，跟踪消息、token 用量与 provider 状态。 |
| `Toolkit` | 可按名称执行工具集合的容器。 |
| `Toolkit Catalog` | 允许运行时激活/停用受管 toolkit 的层。 |
| `Workspace Pin` | 会话级 pinned file context，会在后续 prompt 中重新注入。 |
| `Continuation` | 当运行因为确认或人类输入而暂停时返回的序列化状态。 |
