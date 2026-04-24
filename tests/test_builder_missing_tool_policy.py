import unittest

from unchain.agent.agent import Agent
from unchain.tools import tool


def _noop(**_kwargs):
    return {}


def _read_tool():
    return tool(name="read", description="Read a file.", func=_noop, parameters=[])


def _grep_tool():
    return tool(name="grep", description="Search text.", func=_noop, parameters=[])


class _ToolKitStub:
    def __init__(self, tools):
        self.tools = {t.name: t for t in tools}


class BuilderMissingToolPolicyTests(unittest.TestCase):
    def _make_builder_with_tools(self, spec_allowed_tools, policy):
        from unchain.agent.builder import AgentBuilder, AgentCallContext
        from unchain.agent.model_io import ModelIOFactoryRegistry

        agent = Agent(
            name="test-agent",
            instructions="you are a test agent",
            allowed_tools=spec_allowed_tools,
            missing_tool_policy=policy,
        )
        builder = AgentBuilder(
            agent=agent,
            spec=agent.spec,
            state=agent.state,
            call_context=AgentCallContext(mode="run", input_messages=[]),
            model_io_registry=ModelIOFactoryRegistry(),
        )
        builder.toolkit = _ToolKitStub([_read_tool(), _grep_tool()])
        return builder

    def test_raise_policy_raises_on_missing(self):
        builder = self._make_builder_with_tools(("read", "nonexistent"), "raise")
        with self.assertRaises(ValueError) as ctx:
            builder._apply_allowed_tools_filter()
        self.assertIn("nonexistent", str(ctx.exception))

    def test_warn_skip_policy_drops_missing_and_logs(self):
        builder = self._make_builder_with_tools(("read", "nonexistent"), "warn_skip")
        with self.assertLogs("unchain.agent.builder", level="WARNING") as log_ctx:
            builder._apply_allowed_tools_filter()
        self.assertIn("nonexistent", "\n".join(log_ctx.output))
        self.assertEqual(list(builder.toolkit.tools.keys()), ["read"])

    def test_warn_skip_policy_builds_when_all_present(self):
        builder = self._make_builder_with_tools(("read", "grep"), "warn_skip")
        builder._apply_allowed_tools_filter()
        self.assertEqual(set(builder.toolkit.tools.keys()), {"read", "grep"})

    def test_default_policy_is_raise(self):
        agent = Agent(name="a", allowed_tools=("read", "nonexistent"))
        self.assertEqual(agent.spec.missing_tool_policy, "raise")


if __name__ == "__main__":
    unittest.main()
