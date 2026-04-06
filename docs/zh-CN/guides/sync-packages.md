# 同步包

本指南描述如何检查和同步 `unchain` 包命名空间之间的共享实现。在对共享模块进行修改后使用本指南以确保一致性。

## 前提条件

- 理解双包布局（参见[架构总览](../skills/architecture-overview.md)）
- 了解哪些模块是共享的、哪些是独立的

## 参考文件

| 文件 / 目录 | 职责 |
|-------------|------|
| `src/unchain/tools/` | 工具类、装饰器、模型、注册表、catalog |
| `src/unchain/toolkits/builtin/` | 内置 toolkit 实现 |
| `src/unchain/memory/` | 记忆管理器、配置、策略、存储 |
| `src/unchain/schemas/` | 共享 schema（如 `ResponseFormat`） |
| `src/unchain/runtime/resources/model_capabilities.json` | 模型注册表 |
| `src/unchain/runtime/resources/model_default_payloads.json` | 默认负载 |

## 共享模块

以下区域有并行实现，可能需要同步：

| 区域 | 内容 |
|------|------|
| `tools/` | `Tool`、`Toolkit`、装饰器、模型、注册表、catalog |
| `toolkits/builtin/` | CoreToolkit、ExternalAPIToolkit |
| `memory/` | MemoryManager、MemoryConfig、策略、存储、Qdrant 适配器 |
| `schemas/` | ResponseFormat 及相关 schema |
| 运行时资源 | `model_capabilities.json`、`model_default_payloads.json` |

## 步骤

1. **确定需要同步的模块。** 选择一个特定区域（如 `tools`、`toolkits`、`memory`）或检查所有共享模块。

2. **对比实现差异。** 比较每个共享文件的两个版本，找出有意义的差异（忽略导入路径差异，因为这是预期的）。

3. **确定同步方向。** 对于每个差异：
   - `unchain` 命名空间是否为权威来源？（新代码通常是。）
   - 另一个版本是否为了向后兼容而故意不同？
   - 是否应该同步，还是差异是刻意的？

4. **在适当的地方应用更改**，保留导入路径差异。

5. **运行测试套件**以验证没有破坏任何功能。

## 测试

同步后运行完整测试套件：

```bash
PYTHONPATH=src pytest tests/ -q --tb=short
```

## 相关文档

- [架构总览](../skills/architecture-overview.md) -- 包布局与双命名空间设计
- [测试约定](../skills/testing-conventions.md) -- 如何运行和组织测试
