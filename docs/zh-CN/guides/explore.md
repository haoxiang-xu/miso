# 探索架构

本指南提供 unchain 代码库的地图，按子系统组织。当你需要理解或修改框架的某个特定部分时，可将其作为起点。

## 前提条件

- 对项目结构有基本了解（参见[架构总览](../skills/architecture-overview.md)）

## 架构地图

### Kernel Loop

驱动 agent 运行的核心执行引擎，通过 harness 调度的阶段执行。

| 文件 | 职责 |
|------|------|
| `src/unchain/kernel/loop.py` | `KernelLoop` -- 主执行引擎 |
| `src/unchain/kernel/state.py` | `RunState` -- 可变运行状态，含消息版本管理 |
| `src/unchain/kernel/harness.py` | `BaseRuntimeHarness` 协议 |
| `src/unchain/kernel/delta.py` | `HarnessDelta` -- 不可变状态变更操作 |

### 记忆系统

管理对话记忆、上下文引导和长期存储。

| 文件 | 职责 |
|------|------|
| `src/unchain/memory/runtime.py` | `KernelMemoryRuntime` |
| `src/unchain/memory/manager.py` | `MemoryManager` |
| `src/unchain/memory/config.py` | 记忆配置 |
| `src/unchain/memory/stores.py` | 存储后端 |
| `src/unchain/memory/long_term.py` | 长期记忆持久化 |
| `src/unchain/memory/bootstrap.py` | 引导 harness（上下文注入） |

### 工具执行

工具如何被定义、发现和在 agent 运行中执行。

| 文件 | 职责 |
|------|------|
| `src/unchain/tools/execution.py` | `ToolExecutionHarness` -- 在 kernel 阶段中运行工具 |
| `src/unchain/tools/tool.py` | `Tool` 类与参数推断 |
| `src/unchain/tools/toolkit.py` | `Toolkit` 基类 |
| `src/unchain/tools/confirmation.py` | 工具确认门控 |
| `src/unchain/tools/messages.py` | Provider 特定的工具消息格式化 |

### Provider

LLM provider 集成（OpenAI、Anthropic、Gemini、Ollama）。

| 文件 | 职责 |
|------|------|
| `src/unchain/providers/model_io.py` | Provider 实现与 `_NativeModelIOBase` |
| `src/unchain/agent/model_io.py` | `ModelIOFactoryRegistry` -- provider 名称解析 |

### Agent Builder

面向用户的 API，用于构建和运行 agent。

| 文件 | 职责 |
|------|------|
| `src/unchain/agent/agent.py` | `Agent` 类 -- 主要的用户面向 API |
| `src/unchain/agent/builder.py` | `AgentBuilder` -- 构建 kernel 运行 |
| `src/unchain/agent/spec.py` | Agent 规格定义 |
| `src/unchain/agent/modules/` | 可插拔 agent 模块（Tools、Memory、Policies 等） |

### Subagent

在父运行中生成和协调子 agent。

| 文件 | 职责 |
|------|------|
| `src/unchain/subagents/executor.py` | Subagent 执行 |
| `src/unchain/subagents/plugin.py` | Subagent 插件接口 |
| `src/unchain/subagents/types.py` | Subagent 类型定义 |

### 优化器

上下文优化策略（消息截断、摘要化）。

| 文件 | 职责 |
|------|------|
| `src/unchain/optimizers/base.py` | 优化器基类 |
| `src/unchain/optimizers/last_n.py` | Last-N 消息优化器 |
| `src/unchain/optimizers/llm_summary.py` | 基于 LLM 的摘要优化器 |

### 工作区

文件固定与工作区管理。

| 文件 | 职责 |
|------|------|
| `src/unchain/workspace/pins.py` | 文件固定系统 |

### 运行时资源

模型能力与默认值的静态配置文件。

| 文件 | 职责 |
|------|------|
| `src/unchain/runtime/resources/model_capabilities.json` | 模型注册表，含能力描述 |
| `src/unchain/runtime/resources/model_default_payloads.json` | 每个模型的默认请求负载 |
| `src/unchain/schemas/models.py` | 模型命名常量 |

## 执行流

```
Agent.run()
  -> AgentBuilder
    -> PreparedAgent
      -> KernelLoop.run()
        -> step_once() loop:
          -> dispatch_phase(harnesses)
          -> fetch_model_turn(provider)
          -> tool execution
          -> memory commit
        -> KernelRunResult
```

## 关键扩展点

| 你想做什么 | 从这里开始 |
|-----------|-----------|
| 向执行循环添加行为 | [添加 Harness](add-harness.md) |
| 支持新的 LLM 服务 | [添加 Provider](add-provider.md) |
| 注册新模型 | [添加模型](add-model.md) |
| 添加新工具 | [添加工具](add-tool.md) |
| 创建工具集合 | [添加 Toolkit](add-toolkit.md) |

## 相关文档

- [架构总览](../skills/architecture-overview.md) -- 概念性架构指南
- [Agent 与 Team](../skills/agent-and-team.md) -- agent 组合模式
- [类索引](../appendix/class-index.md) -- 完整类参考
- [导出索引](../appendix/export-index.md) -- 公共 API 导出
- [术语表](../appendix/glossary.md) -- 术语参考
