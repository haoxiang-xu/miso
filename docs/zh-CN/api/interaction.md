# Interaction API 参考

Durable interaction request、receipt、持久化 helper、同步 callback adapter，以及
`unchain.interaction` 导出的 package-level interaction 工具。

Durable interaction kind 包括 `human_input`、`tool_approval` 与 `max_budget`。

## Exposure 与推荐入口

应用代码通常应使用 [`Agent.submit_interaction()` 与
`Agent.resume_interaction()`](agents.md#agent)。不可变数据类型与 typed error 也会从
`unchain.interaction` 导出：

```python
from unchain.interaction import (
    InteractionError,
    InteractionReceipt,
    InteractionRequest,
)
```

持久化 facade、snapshot 与 max-budget callback adapter 刻意不从
`unchain.interaction` 重新导出：

- `DurableInteractionRuntime` 与 `DurableInteractionSnapshot` 是
  `unchain.interaction.runtime` 的 module-level export，供框架和 storage
  integration 使用。
- `DurableMaxBudgetCallbackAdapter` 是 `unchain.interaction.adapters` 的
  module-level export。它是 kernel integration 类型，不是应用层恢复 interaction
  的入口。

## 覆盖表

| 类 | 源码 | Exposure | 类型 |
| --- | --- | --- | --- |
| `InteractionError` | `src/unchain/interaction/durable.py:36` | `unchain.interaction` | error class |
| `InteractionIntegrityError` | `src/unchain/interaction/durable.py:42` | `unchain.interaction` | error class |
| `InteractionNotPendingError` | `src/unchain/interaction/durable.py:48` | `unchain.interaction` | error class |
| `InteractionReceiptConflictError` | `src/unchain/interaction/durable.py:54` | `unchain.interaction` | error class |
| `InteractionAlreadyAppliedError` | `src/unchain/interaction/durable.py:60` | `unchain.interaction` | error class |
| `InteractionRequest` | `src/unchain/interaction/durable.py:214` | `unchain.interaction` | dataclass（frozen、slots） |
| `InteractionReceipt` | `src/unchain/interaction/durable.py:407` | `unchain.interaction` | dataclass（frozen、slots） |
| `DurableInteractionSnapshot` | `src/unchain/interaction/runtime.py:160` | module-only：`unchain.interaction.runtime` | dataclass（frozen、slots） |
| `DurableInteractionRuntime` | `src/unchain/interaction/runtime.py:205` | module-only：`unchain.interaction.runtime` | dataclass |
| `DurableMaxBudgetCallbackAdapter` | `src/unchain/interaction/adapters.py:31` | module-only：`unchain.interaction.adapters` | dataclass |

## Durable 协议

发生 durable suspension 时，框架会把不可变 `InteractionRequest` 与 execution
checkpoint 原子地写入。规范化后的答案随后作为独立 `InteractionReceipt` 保存；在
receipt 持久化前，不会应用 resume delta、调用模型或执行已批准工具。下一次
checkpoint 转换或 semantic commit 会记录 receipt 已被应用。

Request 与 receipt 使用 canonical JSON 和内容 digest。重复提交相同的规范化
response 是幂等的；同一 request 收到不同 response 会 fail closed。这个边界保护的
是 decision，不会自动让任意外部工具副作用 exactly-once。

## InteractionRequest

对一次待处理 human-input、tool-approval 或 max-budget decision 的不可变、
content-addressed 描述。

### 字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `schema_version` | `int` | 当前 durable interaction schema 版本。 |
| `interaction_id` | `str` | 从 `request_digest` 确定性派生的 ID。 |
| `session_id` | `str` | 非空的所属 session ID。 |
| `kind` | `Literal["human_input", "tool_approval", "max_budget"]` | 选择 response normalization contract。 |
| `source_run_id` | `str` | 创建该 request 的 run。 |
| `occurrence` | `str` | 这次 decision occurrence 的稳定身份。 |
| `payload` | `Any` | Strict JSON request payload。 |
| `response_contract` | `Any` | Strict JSON response contract。 |
| `schema_digest` | `str` | `response_contract` 的 canonical digest。 |
| `request_digest` | `str` | request identity 字段的 canonical digest。 |
| `created_revision` | `int` | 创建时的非负 session revision。 |
| `subject` | `Any` | Strict JSON execution binding；默认值 `None`。 |

构造与反序列化会校验精确 schema、受支持的 kind、canonical JSON 值、digest 与
确定性 ID。序列化输入缺字段或带未知字段时会抛出
`InteractionIntegrityError`。

### 公开方法

| 方法 | 返回 | 说明 |
| --- | --- | --- |
| `to_dict()` | `dict[str, Any]` | 深拷贝序列化全部 schema 字段。 |
| `from_dict(raw)` | `InteractionRequest` | 严格反序列化并重新校验 request。 |

### Builder

```python
build_interaction_request(
    *,
    session_id: str,
    kind: InteractionKind,
    source_run_id: str,
    occurrence: str,
    payload: Any,
    response_contract: Any,
    created_revision: int,
    subject: Any = None,
) -> InteractionRequest
```

`build_interaction_request` 从 `unchain.interaction` 导出，会根据规范化输入计算
schema digest、request digest 与 interaction ID。

## InteractionReceipt

绑定到一个 request 的不可变、content-addressed 规范化答案。

### 字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `schema_version` | `int` | 当前 durable interaction schema 版本。 |
| `receipt_id` | `str` | 从 `receipt_digest` 确定性派生的 ID。 |
| `interaction_id` | `str` | 被回答 request 的 ID。 |
| `request_digest` | `str` | 把 receipt 绑定到该 request 的 digest。 |
| `response` | `Any` | Strict JSON 规范化 response。 |
| `response_digest` | `str` | `response` 的 canonical digest。 |
| `submitted_by` | `str` | 非空的提交者身份。 |
| `receipt_digest` | `str` | receipt identity 字段的 canonical digest。 |
| `submitted_at_ms` | `int` | 非负的毫秒级 observation 时间戳。它属于 metadata，不参与 `receipt_digest` 或 `receipt_id` 的计算。 |

### 公开方法

| 方法 | 返回 | 说明 |
| --- | --- | --- |
| `to_dict()` | `dict[str, Any]` | 深拷贝序列化全部 schema 字段。 |
| `from_dict(raw, *, request=None)` | `InteractionReceipt` | 严格反序列化；可额外校验 receipt 是否属于 `request`。 |

### Builder

```python
build_interaction_receipt(
    request: InteractionRequest | dict[str, Any],
    response: Any,
    *,
    submitted_by: str = "user",
    submitted_at_ms: int,
) -> InteractionReceipt
```

`build_interaction_receipt` 从 `unchain.interaction` 导出。应用代码通常应调用
`Agent.submit_interaction()`，由它按 request kind 规范化 response，并使用 session
CAS 语义完成持久化。

## Interaction errors

所有公开 durable interaction error 都继承自 `InteractionError`；后者继承
`RuntimeError`。

| Error | `code` | 含义 |
| --- | --- | --- |
| `InteractionError` | `interaction_error` | Durable interaction failure 基类。 |
| `InteractionIntegrityError` | `interaction_integrity_error` | 持久数据、schema、digest 或 execution binding 格式错误或不一致。 |
| `InteractionNotPendingError` | `interaction_not_pending` | 目标 interaction 不存在、不活跃、已消费，或缺少必需 receipt。 |
| `InteractionReceiptConflictError` | `interaction_receipt_conflict` | 已有 receipt 的 request 又收到不同答案。 |
| `InteractionAlreadyAppliedError` | `interaction_already_applied` | 尝试用不同 application 替换已经应用的 receipt。 |

## DurableInteractionSnapshot

`DurableInteractionRuntime` 的读取结果。它从 `unchain.interaction.runtime` 导出，
不会从 `unchain.interaction` 导出。

### 字段与属性

| 字段/属性 | 类型 | 说明 |
| --- | --- | --- |
| `request` | `InteractionRequest` | 已校验的不可变 request。 |
| `checkpoint_id` | `str` | 与 request 绑定的 checkpoint。 |
| `receipt` | `InteractionReceipt \| None` | 已提交的答案；未提交时为 `None`。 |
| `application` | `dict[str, Any] \| None` | receipt application marker；未应用时为 `None`。 |
| `session_snapshot` | `SessionSnapshot` | 本次读取使用的 revisioned session state。 |
| `response` | `dict[str, Any] \| None` | `receipt.response` 的深拷贝；无 receipt 时为 `None`。 |

## DurableInteractionRuntime

从 journal 读取 interaction，并在 revisioned session document 中记录唯一一份规范化
response 的持久化 facade。它从 `unchain.interaction.runtime` 导出，不会从
`unchain.interaction` 导出；多数 caller 应优先使用 `Agent` 方法。

### 构造函数

```python
DurableInteractionRuntime(
    memory_runtime: KernelMemoryRuntime,
    clock_ms: Callable[[], int] = ...,
)
```

### 公开方法

| 方法 | 返回 | 说明 |
| --- | --- | --- |
| `load(session_id, *, interaction_id=None, require_active=False)` | `DurableInteractionSnapshot` | 加载并校验 active 或历史 journal entry。 |
| `load_active(session_id)` | `DurableInteractionSnapshot` | 加载 session 当前活跃 interaction。 |
| `record_receipt(session_id, *, interaction_id, response, submitted_by="user", expected_revision=None, execution_fence=None)` | `DurableInteractionSnapshot` | 规范化 response 并通过 CAS 持久化；相同重交幂等，冲突提交 fail closed。 |
| `require_receipt(session_id, *, interaction_id=None)` | `DurableInteractionSnapshot` | 要求 active request 已有 canonical persisted receipt。 |

## DurableMaxBudgetCallbackAdapter

当 memory-backed run 同时提供可调用的 `on_max_iterations` 时，`KernelLoop` 使用的
同步 adapter。它只从 `unchain.interaction.adapters` 导出，不是应用层 interruption
API。

`before_wait()` 构造并持久化 max-budget request 与 continuation，释放 execution
lease，并发出 `interaction_requested`。`invoke()` 调用配置的 callback，把规范化
response 持久化为 receipt，更新 session revision，并在返回 response 前重新获取
lease。

它的 `interaction_request` 与 `wait_revision` 字段由 `before_wait()` 填充，不是构造
参数。没有配置 callback path 时，run 返回普通 `max_iterations` 结果，而不是非阻塞
max-budget request。

## 其他 package 导出

`unchain.interaction.__all__` 还包含下列已有 interaction primitive。这里列出它们，
确保 package export surface 明确；具体行为见链接的 API 或 skills 章节。

### 相关 class 导出

| 名称 | 源码 | 参考 |
| --- | --- | --- |
| `HumanInputOption` | `src/unchain/input/human_input.py:94` | [Input API](input-workspace-schemas.md#humaninputoption) |
| `HumanInputRequest` | `src/unchain/input/human_input.py:122` | [Input API](input-workspace-schemas.md#humaninputrequest) |
| `HumanInputResponse` | `src/unchain/input/human_input.py:258` | [Input API](input-workspace-schemas.md#humaninputresponse) |
| `HumanInputResumePlan` | `src/unchain/interaction/resume.py:27` | Legacy human-input continuation plan。 |
| `HumanInputResumeHarness` | `src/unchain/interaction/resume.py:191` | Legacy human-input `on_resume` harness。 |
| `FyiMessage` | `src/unchain/interaction/fyi.py:14` | Mid-run FYI message value。 |
| `FyiChannel` | `src/unchain/interaction/fyi.py:20` | Thread-safe mid-run FYI channel。 |
| `FyiInjectionHarness` | `src/unchain/interaction/fyi.py:76` | 在 `before_model` 注入排队的 FYI message。 |
| `ProgressDigest` | `src/unchain/interaction/btw.py:18` | 为 side question 收集有界 callback digest。 |
| `QueuedTurnBuffer` | `src/unchain/interaction/queue_turns.py:31` | Run 结束后 drain 的 thread-safe follow-up buffer。 |

### 常量

| 名称 | 源码 | 用途 |
| --- | --- | --- |
| `ASK_USER_QUESTION_TOOL_NAME` | `src/unchain/input/human_input.py` | Canonical human-input tool name。 |
| `HUMAN_INPUT_KIND_SELECTOR` | `src/unchain/input/human_input.py` | Selector request kind。 |
| `HUMAN_INPUT_OTHER_VALUE` | `src/unchain/input/human_input.py` | Canonical free-form option value。 |
| `INTERACTION_EFFECT_CREATED_BY` | `src/unchain/interaction/effects.py` | Durable interaction delta creator identity。 |
| `INTERACTION_JOURNAL_KEY` | `src/unchain/interaction/durable.py` | Interaction journal 的 session document key。 |
| `INTERACTION_KIND_HUMAN_INPUT` | `src/unchain/interaction/durable.py` | `human_input` kind 常量。 |
| `INTERACTION_KIND_TOOL_APPROVAL` | `src/unchain/interaction/durable.py` | `tool_approval` kind 常量。 |
| `INTERACTION_KIND_MAX_BUDGET` | `src/unchain/interaction/durable.py` | `max_budget` kind 常量。 |

### Builder 与 helper 导出

| 名称 | 源码 | 作用 |
| --- | --- | --- |
| `build_ask_user_question_tool` | `src/unchain/input/human_input.py` | 构造 canonical human-input tool。 |
| `is_human_input_tool_name` | `src/unchain/input/human_input.py` | 判断 canonical human-input tool name。 |
| `build_interaction_request` | `src/unchain/interaction/durable.py` | 构造已校验的不可变 request。 |
| `build_interaction_receipt` | `src/unchain/interaction/durable.py` | 构造绑定到 request 的已校验 receipt。 |
| `build_human_input_continuation` | `src/unchain/interaction/effects.py` | 构造 human-input continuation payload。 |
| `build_human_input_requested_event` | `src/unchain/interaction/effects.py` | 构造 legacy human-input event。 |
| `build_human_input_suspend_request` | `src/unchain/interaction/effects.py` | 构造 human-input suspend operation。 |
| `build_tool_approval_continuation` | `src/unchain/interaction/effects.py` | 构造 tool-approval continuation payload。 |
| `build_tool_approval_suspend_request` | `src/unchain/interaction/effects.py` | 构造 tool-approval suspend operation。 |
| `build_max_budget_continuation` | `src/unchain/interaction/effects.py` | 构造 max-budget continuation payload。 |
| `build_max_budget_suspend_request` | `src/unchain/interaction/effects.py` | 构造 max-budget suspend operation。 |
| `parse_human_input_request` | `src/unchain/interaction/resume.py` | 解析 human-input tool call。 |
| `prepare_human_input_resume_plan` | `src/unchain/interaction/resume.py` | 校验并准备 legacy resume plan。 |
| `hydrate_human_input_resume_state` | `src/unchain/interaction/resume.py` | 从该 plan 恢复 state。 |
| `wrap_fyi` | `src/unchain/interaction/fyi.py` | 把 FYI message 投影进 model context。 |
| `build_btw_prompt` | `src/unchain/interaction/btw.py` | 根据 progress 构造 side-question prompt。 |
| `merge_queued_turn_texts` | `src/unchain/interaction/queue_turns.py` | 按顺序合并 queued follow-up。 |
