# 测试约定

`testing-conventions` 主题的正式简体中文 skills 章节。

## 角色与边界

本章说明仓库如何在不依赖真实 provider 的前提下测试编排、工具执行、memory、registry 行为和 eval fixture。

## 依赖关系

- 单元测试主要 monkeypatch provider fetch 或使用 fake adapter。
- toolkit discovery 测试会生成临时 manifest package。
- notebook/eval fixture 与主单测树并列但相互隔离。

## 核心对象

- `ProviderTurnResult`
- `ToolCall`
- `MemoryManager` fake
- `ToolkitRegistry` fixture
- `EvalCase`/`JudgeReport` in `tests/evals/`

## 执行流与状态流

- patch provider fetch 来模拟 tool turn。
- 用 fake store/adapter 获得确定性的 memory 行为。
- 把 eval fixture 与单元测试分开运行。
- 通过临时 package 和 tmp path 验证新 toolkit manifest 与安全约束。

## 配置面

- Python 3.12 virtualenv 与 editable install。
- `pyproject.toml` 中的 pytest 发现配置。
- 通过 `pytest -k` 或单文件运行做定点调试。

## 扩展点

- 优先新增 fake adapter，而不是调用真实服务。
- 在 `tests/evals/` 下增加端到端行为用例。
- 保持测试 helper 命名与 fixture 布局一致。

## 常见陷阱

- `tests/evals/fixtures` 被有意排除在 pytest discovery 之外。
- 多轮 provider 行为经常用 state dict 建模。
- 验证事件顺序时，callback 断言往往是唯一可靠手段。

## 关联 class 参考

- [Runtime API](../api/runtime.md)
- [Memory API](../api/memory.md)
- [Tool System API](../api/tools.md)

## 源码入口

- `tests/`
- `tests/evals/`
- `pyproject.toml`

## 详细的遗留参考

以下保留了原始仓库 skill 笔记，用于延续性与额外示例。规范副本现已迁入此文档树。

> 测试组织、命名、mock 模式、无 LLM 的 agent 测试、eval 框架以及如何运行测试。

## 运行测试

```bash
# 一次性设置
./scripts/init_python312_venv.sh   # 创建 Python 3.12 的 .venv
pip install -e ".[dev]"            # 带 dev 依赖的 editable install

# 运行所有测试
./run_tests.sh

# 运行特定测试
python -m pytest tests/test_agent_core.py -v

# 按模式运行测试
python -m pytest -k "test_tool_parameter" -v
```

`run_tests.sh` 在运行前会校验 Python 3.12、pytest 以及 unchain 是否已安装。

### pytest 配置

来自 `pyproject.toml`：

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
norecursedirs = [".git", ".pytest_cache", ".venv", "tests/evals/fixtures"]
```

注意: `tests/evals/fixtures/` 被排除 -- 那里是 eval 用例的 workspace fixture，不是测试文件。

## 文件与函数命名

| 约定            | 模式                             | 示例                                          |
| --------------- | -------------------------------- | --------------------------------------------- |
| 测试文件        | `test_<feature>.py`              | `test_agent_core.py`, `test_memory.py`        |
| 测试函数        | `test_<what_it_tests>()`         | `test_tool_parameter_inference_and_execute()` |
| 辅助函数        | `_<name>()` (前导下划线)         | `_tool_turn()`, `_final_turn()`               |
| Fake 类         | `_Fake<Interface>`               | `_FakeVectorAdapter`, `_FakeProfileStore`     |

所有测试文件位于顶层 `tests/` 目录 (扁平结构，单元测试无子目录)。

## Mock 模式

### 模式 1: Mock `_fetch_once` 进行 Agent 测试

最常见的模式 -- 不发起 LLM 调用即可测试 agent 逻辑：

```python
from unchain.runtime import ProviderTurnResult, ToolCall

def _tool_turn(tool_name, arguments):
    """模拟调用工具的 LLM turn。"""
    return ProviderTurnResult(
        assistant_message={"role": "assistant", "content": None},
        tool_calls=[ToolCall(id="call_1", name=tool_name, arguments=arguments)],
        token_usage={"prompt_tokens": 100, "completion_tokens": 50},
    )

def _final_turn(content):
    """模拟返回最终答案的 LLM turn。"""
    return ProviderTurnResult(
        assistant_message={"role": "assistant", "content": content},
        tool_calls=[],
        token_usage={"prompt_tokens": 100, "completion_tokens": 50},
    )

def test_agent_calls_tool(monkeypatch):
    agent = Agent(name="test", provider="openai", model="gpt-5", tools=[...])

    state = {"turn": 0}
    def fake_fetch(messages, tools, **kwargs):
        state["turn"] += 1
        if state["turn"] == 1:
            return _tool_turn("read_files", {"paths": ["main.py"]})
        return _final_turn("Done reading the file.")

    monkeypatch.setattr(agent, "_fetch_once", fake_fetch)
    messages, bundle = agent.run("Read main.py")
    assert "Done reading" in messages[-1]["content"]
```

### 模式 2: Fake Adapter 进行 Memory 测试

```python
class _FakeVectorAdapter:
    def __init__(self):
        self.added = []         # 追踪索引了什么
        self.searches = []      # 追踪搜索了什么

    def add(self, texts, metadatas, namespace):
        self.added.append({"texts": texts, "metadatas": metadatas, "namespace": namespace})

    def search(self, query, top_k, namespace):
        self.searches.append({"query": query, "top_k": top_k})
        return []  # 测试中返回空

class _FakeLongTermProfileStore:
    def __init__(self):
        self.profiles = {}

    def load(self, namespace):
        return self.profiles.get(namespace, {})

    def save(self, namespace, profile):
        self.profiles[namespace] = profile
```

### 模式 3: State Dict 进行多轮序列

```python
def test_multi_turn_flow(monkeypatch):
    state = {"turn": 0}

    def fake_fetch(messages, tools, **kwargs):
        state["turn"] += 1
        if state["turn"] == 1:
            return _tool_turn("list_directories", {"paths": ["."]})
        elif state["turn"] == 2:
            return _tool_turn("read_files", {"paths": ["README.md"]})
        else:
            return _final_turn("Here is the summary...")

    # ... 测试代码 ...
```

### 模式 4: 事件 Callback 断言

```python
def test_events_emitted(monkeypatch):
    events = []

    def capture(event):
        events.append(event)

    messages, bundle = agent.run("task", callback=capture)

    event_types = [e["type"] for e in events]
    assert "run_started" in event_types
    assert event_types.count("tool_result") == 2
    assert "run_completed" in event_types
```

### 模式 5: 使用 `tmp_path` 进行文件系统测试

```python
def test_workspace_tool(tmp_path):
    # 创建测试文件
    (tmp_path / "hello.txt").write_text("world")
    (tmp_path / "subdir").mkdir()

    tk = WorkspaceToolkit(workspace_root=str(tmp_path))
    result = tk.execute("read_files", {"paths": ["hello.txt"]})
    assert result["files"][0]["content"] == "world"
```

### 模式 6: 使用临时包进行 Toolkit Discovery

```python
def _write_toolkit_package(root, toolkit_id, tools):
    """辅助: 为 discovery 测试创建最小 toolkit 包。"""
    pkg_dir = root / toolkit_id
    pkg_dir.mkdir()

    # 写入 toolkit.toml
    toml_content = f"""
[toolkit]
id = "{toolkit_id}"
name = "Test {toolkit_id}"
description = "Test toolkit"
factory = "test_pkg.{toolkit_id}:Factory"
readme = "README.md"
"""
    for tool_name in tools:
        toml_content += f"""
[[tools]]
name = "{tool_name}"
description = "Test tool"
"""
    (pkg_dir / "toolkit.toml").write_text(toml_content)
    (pkg_dir / "README.md").write_text("# Test")
    # ... 写入 factory 模块 ...
```

## 每个组件应测试什么

### 新 Builtin Toolkit

- [ ] 所有工具正确注册 (名称与 manifest 匹配)
- [ ] 参数推断生成正确的 JSON schema
- [ ] 每个工具在成功时返回预期的 dict 形状
- [ ] 每个工具在失败时返回 `{"error": ...}`
- [ ] 路径安全: 穿越尝试抛出 `ValueError`
- [ ] `shutdown()` 清理资源
- [ ] history optimizer 减小载荷大小

### 新工具

- [ ] 从签名正确推断参数类型
- [ ] 提取 docstring 描述
- [ ] 必填 vs 可选参数正确
- [ ] `observe` 和 `requires_confirmation` 标志传播正确
- [ ] `execute()` 处理边界情况 (空参数、错误类型)

### Memory 配置

- [ ] MemoryConfig 验证字段范围
- [ ] 上下文策略选择正确的消息
- [ ] session store 正确持久化/加载
- [ ] 工具历史压缩缩减载荷
- [ ] namespace 隔离在 agent 之间有效

### Agent 集成

- [ ] 混合输入的工具正确合并
- [ ] memory 配置从 dict 正确转换为 dataclass
- [ ] callback 按顺序接收预期事件
- [ ] 暂停与恢复保持状态
- [ ] catalog state token 跨暂停存活

## Eval 框架 (`tests/evals/`)

eval 框架通过基于 LLM 的判定来基准测试 agent 性能。

### 核心类型

```python
from tests.evals.types import EvalCase, RunArtifact, JudgeReport

case = EvalCase(
    task_id="file_inspection",
    prompt="Read main.py and explain it.",
    workspace_config={"root": "./fixtures/simple_repo"},
    allowed_toolkits=["workspace", "terminal"],
    rules={
        "required_paths": ["main.py"],
        "required_tool_names": ["read_files"],
        "forbidden_tool_names": ["write_file", "delete_lines"],
        "min_tool_calls": 1,
        "min_final_chars": 100,
    },
    rubric_weights={
        "correctness": 40,
        "debugging": 25,
        "tool_strategy": 20,
        "efficiency": 15,
    },
)
```

### 基于规则的检查

| 规则                       | 检查内容                                   |
| -------------------------- | ------------------------------------------ |
| `required_paths`           | Agent 必须引用这些文件路径                 |
| `required_substrings`      | 最终答案必须包含这些字符串                 |
| `required_regexes`         | 最终答案必须匹配这些模式                   |
| `required_tool_names`      | 这些工具必须被调用                         |
| `required_tool_any_of`     | 至少调用其中一个工具                       |
| `forbidden_tool_names`     | 这些工具必须不被调用                       |
| `min_tool_calls`           | 最少工具调用次数                           |
| `min_final_chars`          | 最终答案的最小长度                         |
| `min_file_reference_count` | 答案中的最少文件引用数                     |

### 工作流程

```text
1. 用 task、workspace、rules、rubric 定义 EvalCase
2. Runner 使用 case 配置构建 agent
3. Agent 执行，收集 messages + events
4. 创建包含执行元数据 + 规则分数的 RunArtifact
5. Judge agent (LLM) 使用 rubric weights 评估
6. 返回包含分数 + 建议的 JudgeReport
```

### 基于 Notebook 的 Eval

eval 用例也可以作为 Jupyter notebook 运行：

```text
tests/evals/
├── notebooks/           # 可执行 notebook
│   └── tetris_beginner_game_test/
├── templates/
│   └── single_test_template.ipynb    # 复制以创建新 eval
├── fixtures/            # Eval 用例的 workspace fixture
│   ├── fixture_debug/
│   └── multi_file_plan/
└── artifacts/           # Eval 运行的输出
    └── <test_id>/<timestamp>/
```

创建新 eval：将 `single_test_template.ipynb` 复制到 `notebooks/`，然后在 notebook cell 中配置 `MODEL_SPEC`、`EVAL_RULES`、`TASK_PROMPT`、`WORKSPACE_CONFIG` 和 `TOOLKIT_CONFIG`。

## 常见测试陷阱

1. **不要忘记 `monkeypatch`** -- Agent 测试应 mock `_fetch_once`，而非发起真正的 API 调用。真实调用放在 smoke test (`test_*_smoke.py`)。

2. **文件系统测试用 `tmp_path`** -- 始终使用 pytest 的 `tmp_path` fixture，不要使用真实 workspace。

3. **用 state dict 控制轮次** -- 使用 `state = {"turn": 0}` 并在 mock 中递增来控制多轮行为。

4. **Eval fixture 被排除在 pytest 外** -- `norecursedirs` 跳过 `tests/evals/fixtures/`。不要在那里放测试文件。

5. **Smoke test 需要 API key** -- `test_openai_family_smoke.py` 和 `test_anthropic_smoke.py` 等文件需要真实 API key。在 CI 中如果未配置 key 应跳过它们。

6. **Toolkit manifest 校验在 discovery 时执行** -- 如果测试创建了 manifest 有误的 toolkit，错误发生在 discovery 时而非执行时。

## 相关 Skills

- [creating-builtin-toolkits.md](creating-builtin-toolkits.md) -- 新增 toolkit 时应测试什么
- [tool-system-patterns.md](tool-system-patterns.md) -- 需要验证的工具参数推断
- [memory-system.md](memory-system.md) -- Memory adapter fake 模式
- [agent-and-team.md](agent-and-team.md) -- Agent 集成测试模式
