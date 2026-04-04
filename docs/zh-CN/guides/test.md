# 运行测试

本指南介绍如何运行 unchain 测试套件、筛选测试以及处理测试失败。

## 前提条件

- 已安装 Python 3.12+
- 已安装项目依赖
- 源码树位于 `src/`

## 参考文件

| 文件 / 目录 | 职责 |
|-------------|------|
| `tests/` | 测试目录 |
| `tests/test_kernel_core.py` | Kernel loop 与 harness 测试 |
| `pyproject.toml` | 测试依赖与 pytest 配置 |

## 运行所有测试

运行完整测试套件，排除已知的不稳定测试：

```bash
PYTHONPATH=src pytest tests/ -q --tb=short -k "not read_file_ast and not relocate_non_python_ranges"
```

排除的两个测试是已知不稳定的：
- `test_read_file_ast_parses_python_file`
- `test_pinned_prompt_messages_relocate_non_python_ranges_via_declaration_metadata`

## 运行筛选测试

按特定关键字模式运行匹配的测试：

```bash
PYTHONPATH=src pytest tests/ -q --tb=short -k "<pattern>"
```

常用筛选模式：

| 模式 | 运行内容 |
|------|---------|
| `kernel` | Kernel loop 与 harness 测试 |
| `model` | 模型能力与注册表测试 |
| `memory` | 记忆系统测试 |
| `toolkit` | Toolkit 测试 |
| `anthropic` | Anthropic provider 测试 |
| `openai` | OpenAI provider 测试 |

## 处理测试失败

当测试失败时：

1. **阅读失败的测试文件**，理解断言的内容。
2. **定位根本原因** -- 是代码变更导致测试失败，还是测试本身需要更新？
3. **修复问题**，在源码或测试中进行修改。
4. **重新运行特定的失败测试**以确认修复：
   ```bash
   PYTHONPATH=src pytest tests/<test_file>.py -v --tb=long -k "<test_name>"
   ```

## 测试模式

项目使用 fake client 进行 provider 测试（`FakeOpenAIClient`、`FakeAnthropicClient`）。这些 fake client 的 `__init__` 必须接受 `**kwargs`，以保持与真实 SDK 签名的兼容性。

`tests/evals/` 中提供了评估框架，用于更全面的评估类测试。

## 相关文档

- [测试约定](../skills/testing-conventions.md) -- 完整的测试约定与模式
- [术语表](../appendix/glossary.md) -- 术语参考
