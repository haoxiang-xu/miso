import importlib

import unchain


def test_unchain_top_level_exports():
    from unchain.agent.agent import Agent as UnchainAgent
    assert unchain.Agent is UnchainAgent
    assert unchain.__version__ == "0.2.0"
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
    assert hasattr(memory, "KernelMemoryRuntime")
    assert hasattr(optimizers, "LastNOptimizer")
    assert hasattr(providers_pkg, "OpenAIModelIO")
    assert hasattr(subagents, "SubagentPolicy")
    assert hasattr(tools, "Toolkit")
    assert hasattr(toolkits, "CodeToolkit")
    assert hasattr(runtime, "load_model_capabilities")
    assert "/src/unchain/kernel/" in kernel.__file__
    assert "/src/unchain/agent/" in agent.__file__
    assert "/src/unchain/memory/" in memory.__file__
    assert "/src/unchain/optimizers/" in optimizers.__file__
    assert "/src/unchain/providers/" in providers_pkg.__file__
    assert "/src/unchain/subagents/" in subagents.__file__
    assert "/src/unchain/tools/" in tools.__file__
    assert "/src/unchain/toolkits/" in toolkits.__file__
