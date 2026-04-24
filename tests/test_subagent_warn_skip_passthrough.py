import unittest

from unchain.agent.agent import Agent
from unchain.subagents.types import SubagentTemplate


class SubagentForkPassesWarnSkipTest(unittest.TestCase):
    def test_fork_for_subagent_forwards_warn_skip_when_given(self):
        parent = Agent(name="parent", instructions="p")
        template = SubagentTemplate(
            name="child",
            description="c",
            agent=parent,
            allowed_tools=("nonexistent",),
        )
        child = template.agent.fork_for_subagent(
            subagent_name="child_1",
            mode="delegate",
            parent_name="parent",
            lineage=["parent", "child_1"],
            task="do thing",
            instructions="",
            expected_output="",
            memory_policy="ephemeral",
            allowed_tools=template.allowed_tools,
            missing_tool_policy="warn_skip",
        )
        self.assertEqual(child.spec.missing_tool_policy, "warn_skip")
        self.assertEqual(child.spec.allowed_tools, ("nonexistent",))


if __name__ == "__main__":
    unittest.main()
