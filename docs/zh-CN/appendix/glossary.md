# 术语表

| 术语 | 含义 |
| --- | --- |
| `Agent` | 高层单 Agent 门面，用来配置 tools、memory 和 runtime 选项。 |
| `Broth` | 低层执行引擎，负责 provider turn 与工具循环。 |
| `Toolkit` | 可按名称执行工具集合的容器。 |
| `Toolkit Catalog` | 允许运行时激活/停用受管 toolkit 的层。 |
| `Workspace Pin` | 会话级 pinned file context，会在后续 prompt 中重新注入。 |
| `Continuation` | 当运行因为确认或人类输入而暂停时返回的序列化状态。 |
| `Pupu` | 用于目录化、导入、持久化和挂载 MCP server 的可选子系统。 |
