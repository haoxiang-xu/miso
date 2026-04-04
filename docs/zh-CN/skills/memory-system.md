# Memory 系统

`memory-system` 主题的正式简体中文 skills 章节。

## 角色与边界

本章说明短期上下文选择、长期资料提取、向量检索和 workspace pin 状态如何通过 `MemoryManager` 协同。

## 依赖关系

- 策略实现 `ContextStrategy` 协议。
- `MemoryManager` 协调 store、strategy、摘要生成、向量检索和长期提取。
- 可选的 Qdrant adapter 为会话级与长期检索提供具体向量后端。

## 核心对象

- `MemoryManager`
- `MemoryConfig`
- `LongTermMemoryConfig`
- `LastNTurnsStrategy`
- `SummaryTokenStrategy`
- `HybridContextStrategy`
- `QdrantVectorAdapter`
- `QdrantLongTermVectorAdapter`

## 执行流与状态流

- 依据会话状态和策略规则准备输入消息。
- 注入 summary、相似检索结果和 pinned context。
- 在一轮完成后提交新的会话状态。
- 若已配置，则持久化长期事实和向量嵌入。

## 配置面

- session store 与 vector adapter。
- summary 阈值与 token 限制。
- 长期 namespace、提取模型和持久化目录。

## 扩展点

- 实现自定义 `SessionStore`、`VectorStoreAdapter` 或 `ContextStrategy`。
- 在内存、JSON、Qdrant 后端之间切换。
- 分别调整检索、摘要和长期提取策略。

## 常见陷阱

- namespace 直接影响长期隔离边界。
- 长期组件在真正需要前是可选的。
- Hybrid 只有在配置 vector adapter 后才会提供检索补充。

## 关联 class 参考

- [Memory API](../api/memory.md)
- [Runtime API](../api/runtime.md)

## 源码入口

- `src/unchain/memory/manager.py`
- `src/unchain/memory/qdrant.py`

## 详细说明

本章与英文版保持相同的阅读顺序，但把重点放在结构、调用链和对象边界上；API 级细节请与相邻的参考页配套阅读。

- Agents API 参考: `../api/agents.md`
- Runtime API 参考: `../api/runtime.md`
- 工具系统 API 参考: `../api/tools.md`
- Toolkit 实现参考: `../api/toolkits.md`
- Memory API 参考: `../api/memory.md`
- Input、Workspace 与 Schema 参考: `../api/input-workspace-schemas.md`
