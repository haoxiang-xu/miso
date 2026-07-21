import importlib
from pathlib import Path

import unchain


def test_unchain_top_level_exports():
    from unchain.agent.agent import Agent as UnchainAgent
    assert unchain.Agent is UnchainAgent
    assert unchain.__version__.count(".") == 2
    assert unchain.__brand__ == "unchain"
    assert unchain.__tagline__ == "unchain harness"


def test_unchain_common_subpackages_are_available():
    kernel = importlib.import_module("unchain.kernel")
    agent = importlib.import_module("unchain.agent")
    memory = importlib.import_module("unchain.memory")
    optimizers = importlib.import_module("unchain.optimizers")
    providers_pkg = importlib.import_module("unchain.providers")
    subagents = importlib.import_module("unchain.subagents")
    tools = importlib.import_module("unchain.tools")
    toolkits = importlib.import_module("unchain.toolkits")
    runtime = importlib.import_module("unchain.runtime")

    assert hasattr(kernel, "KernelLoop")
    assert hasattr(agent, "Agent")
    assert hasattr(agent, "SubagentModule")
    assert hasattr(agent, "ToolDiscoveryModule")
    assert hasattr(memory, "KernelMemoryRuntime")
    assert hasattr(optimizers, "LastNOptimizer")
    assert hasattr(providers_pkg, "OpenAIModelIO")
    assert hasattr(subagents, "SubagentPolicy")
    assert hasattr(tools, "Toolkit")
    assert hasattr(tools, "ToolPromptHarness")
    assert hasattr(toolkits, "AgentReachToolkit")
    assert hasattr(toolkits, "CoreToolkit")
    assert not hasattr(toolkits, "CodeToolkit")
    assert not hasattr(toolkits, "AskUserToolkit")
    assert hasattr(runtime, "load_model_capabilities")
    source_root = Path(__file__).resolve().parents[1] / "src" / "unchain"
    for package_name, package in (
        ("kernel", kernel),
        ("agent", agent),
        ("memory", memory),
        ("optimizers", optimizers),
        ("providers", providers_pkg),
        ("subagents", subagents),
        ("tools", tools),
        ("toolkits", toolkits),
    ):
        assert Path(package.__file__).resolve().parent == source_root / package_name
