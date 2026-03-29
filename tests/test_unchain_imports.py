import importlib

import miso
import unchain


def test_unchain_top_level_aliases_miso_exports():
    assert unchain.Agent is miso.Agent
    assert unchain.Team is miso.Team
    assert unchain.__version__ == miso.__version__
    assert unchain.__brand__ == "unchain"
    assert unchain.__tagline__ == "unchain harness"


def test_unchain_common_subpackages_are_available():
    kernel = importlib.import_module("unchain.kernel")
    agent = importlib.import_module("unchain.agent")
    subagents = importlib.import_module("unchain.subagents")
    tools = importlib.import_module("unchain.tools")
    runtime = importlib.import_module("unchain.runtime")
    providers = importlib.import_module("unchain.runtime.providers")

    assert hasattr(kernel, "KernelLoop")
    assert hasattr(agent, "Agent")
    assert hasattr(agent, "SubagentModule")
    assert hasattr(subagents, "SubagentPolicy")
    assert hasattr(tools, "Toolkit")
    assert hasattr(runtime, "Broth")
    assert providers is importlib.import_module("miso.runtime.providers")
    assert "/src/unchain/kernel/" in kernel.__file__
    assert "/src/unchain/agent/" in agent.__file__
    assert "/src/unchain/subagents/" in subagents.__file__
