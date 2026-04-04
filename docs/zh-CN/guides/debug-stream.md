# 调试流式传输问题

本指南帮助你诊断 unchain 运行时栈中聊天流卡住、报错或无法完成的原因。涵盖从前端经过 kernel 到 LLM provider 的完整请求路径。

## 前提条件

- 可访问应用日志
- 理解请求流程（参见[架构总览](../skills/architecture-overview.md)）

## 参考文件

| 文件 | 职责 |
|------|------|
| `src/unchain/providers/model_io.py` | Provider fetch 实现（OpenAI、Anthropic、Ollama） |
| `src/unchain/kernel/loop.py` | Kernel loop 步骤调度 |
| `src/unchain/tools/execution.py` | 工具执行 harness |
| `src/unchain/tools/confirmation.py` | 工具确认门控（阻塞等待） |
| `src/unchain/runtime/resources/model_capabilities.json` | 模型上下文窗口限制 |

## 步骤

### 1. 确定问题所在层

将症状匹配到可能的层：

| 症状 | 可能的层 | 查看位置 |
|------|---------|---------|
| 流完全没有启动 | 前端 / IPC | 入口点（adapter 或 route handler） |
| 流已启动但没有 token 出现 | Provider fetch | `src/unchain/providers/model_io.py` |
| 工具调用后卡住 | 工具确认 | `src/unchain/tools/confirmation.py`、`src/unchain/tools/execution.py` |
| 工具结果返回后没有继续 | Kernel loop | `src/unchain/kernel/loop.py`（max_iterations） |
| 错误未传达给用户 | SSE / 事件解析 | 事件序列化层 |

### 2. 追踪请求路径

请求在栈中的完整路径：

```
Frontend / client
  -> Route handler / adapter
    -> Agent.run()
      -> KernelLoop.run()
        -> step_once() loop:
          -> dispatch_phase(harnesses)
          -> fetch_model_turn(provider)  [src/unchain/providers/model_io.py]
          -> tool execution              [src/unchain/tools/execution.py]
          -> memory commit
        -> KernelRunResult
```

### 3. 检查常见问题

- **上下文窗口溢出：** 将消息总 token 数加上工具定义的 token 数，与 `model_capabilities.json` 中模型的 `max_context_window_tokens` 进行对比。
- **缺少 API 密钥：** 验证 provider 的 API 密钥环境变量已正确设置。
- **工具确认死锁：** 确认门控使用 `threading.Event.wait()`。如果未设置超时且前端从未响应，流将挂起。
- **Provider SDK 超时 / 重试：** 部分 SDK 有激进的重试策略，可能在暴露错误之前导致长时间延迟。
- **SSE 解析边界情况：** 服务器发送事件中缺少 `\n\n` 分隔符，可能导致客户端解析器无限缓冲。

### 4. 诊断与修复

1. 阅读步骤 1 中确定的相关源文件。
2. 如果可疑故障点尚未添加日志，则添加日志。
3. 使用最小化 agent 配置复现问题。
4. 结合具体文件和行号进行修复。

## 测试

修复后运行完整测试套件：

```bash
PYTHONPATH=src pytest tests/ -q --tb=short
```

## 相关文档

- [架构总览](../skills/architecture-overview.md) -- 完整系统数据流
- [运行时引擎](../skills/runtime-engine.md) -- 引擎内部机制与流式行为
- [Agent API 参考](../api/agents.md) -- agent 运行配置
- [术语表](../appendix/glossary.md) -- 术语参考
