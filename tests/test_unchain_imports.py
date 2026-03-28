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
    tools = importlib.import_module("unchain.tools")
    runtime = importlib.import_module("unchain.runtime")
    providers = importlib.import_module("unchain.runtime.providers")

    assert hasattr(kernel, "KernelLoop")
    assert hasattr(tools, "Toolkit")
    assert hasattr(runtime, "Broth")
    assert providers is importlib.import_module("miso.runtime.providers")
