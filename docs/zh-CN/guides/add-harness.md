# 添加新的 Kernel Harness

本指南将引导你为 unchain kernel loop 创建一个新的运行时 harness。Harness 是在 agent 执行周期的特定阶段注入行为的主要扩展点。

## 前提条件

- 熟悉 kernel loop 执行模型（参见[架构总览](../skills/architecture-overview.md)）
- 理解 `HarnessDelta` 不可变状态变更（参见[返回类型与状态流](../appendix/return-shapes-and-state-flow.md)）

## 参考文件

| 文件 | 职责 |
|------|------|
| `src/unchain/kernel/harness.py` | `BaseRuntimeHarness` 协议定义 |
| `src/unchain/kernel/loop.py` | `KernelLoop` 阶段调度逻辑 |
| `src/unchain/kernel/delta.py` | `HarnessDelta` 操作（append、insert、replace、delete） |
| `src/unchain/kernel/state.py` | `RunState` 可变状态，含消息版本管理 |

## 步骤

1. **学习 harness 协议。** 阅读 `src/unchain/kernel/harness.py`，理解 `BaseRuntimeHarness` 接口以及每个 harness 必须满足的契约。

2. **理解阶段调度。** 阅读 `src/unchain/kernel/loop.py`，了解 harness 在 kernel loop 的各个阶段（如 `before_model`、`on_tool_call`、`after_tool_batch`）如何被调度。

3. **参考现有 harness** 的实现模式：
   - **简单（单阶段）：** `src/unchain/memory/bootstrap.py` -- 在 `bootstrap` 阶段运行
   - **复杂（多阶段）：** `src/unchain/tools/execution.py` -- 挂接到 `on_tool_call` 和 `after_tool_batch`
   - **优化器：** `src/unchain/optimizers/last_n.py` -- 在 `before_model` 阶段运行

4. **学习 HarnessDelta 操作。** 阅读 `src/unchain/kernel/delta.py`，了解可用的 delta 操作：`state_only`、`append_messages`、`insert_messages`、`replace_messages` 和 `delete_messages`。

5. **创建你的 harness 类**（参见下方模板）。

6. **注册 harness**，将其添加到对应的模块或 agent builder 中。

7. **编写测试**，参考 `tests/test_kernel_core.py` 中的 kernel 测试模式。

## 模板

```python
from unchain.kernel import BaseRuntimeHarness, HarnessContext, HarnessDelta


class MyHarness(BaseRuntimeHarness):
    name = "my_harness"
    phases = ("before_model",)  # Which phases to run in
    order = 100  # Execution order (lower = earlier)

    def build_delta(self, context: HarnessContext) -> HarnessDelta | None:
        # Access state: context.state, context.latest_messages()
        # Return None to skip, or a delta to apply
        return HarnessDelta.state_only(
            created_by=self.name,
            state_updates={"my_key": value},
        )
```

### 要点

- **`phases`**：该 harness 参与的阶段名称元组。常见阶段：`bootstrap`、`before_model`、`on_tool_call`、`after_tool_batch`。
- **`order`**：整数，控制同一阶段内的执行优先级。数值越小越先执行。
- **`build_delta`**：核心方法。返回 `None` 表示不做操作，或返回 `HarnessDelta` 来变更运行状态。

## 测试

使用标准测试命令运行测试：

```bash
PYTHONPATH=src pytest tests/ -q --tb=short
```

仅运行 kernel 相关测试：

```bash
PYTHONPATH=src pytest tests/ -q --tb=short -k "kernel"
```

## 相关文档

- [架构总览](../skills/architecture-overview.md) -- kernel loop 执行模型
- [运行时引擎](../skills/runtime-engine.md) -- 引擎内部机制
- [返回类型与状态流](../appendix/return-shapes-and-state-flow.md) -- delta 与状态流详解
- [类索引](../appendix/class-index.md) -- 完整类参考
