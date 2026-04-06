from unchain.input import ASK_USER_QUESTION_TOOL_NAME
from unchain.tools import render_tool_prompt_block
from unchain.toolkits import CoreToolkit


def test_ask_user_question_description_encourages_asking_when_multiple_paths_exist():
    tk = CoreToolkit(workspace_root=".")

    tool_json = next(item for item in tk.to_json() if item["name"] == ASK_USER_QUESTION_TOOL_NAME)
    description = tool_json["description"]
    question_description = tool_json["parameters"]["properties"]["question"]["description"]
    options_description = tool_json["parameters"]["properties"]["options"]["description"]
    prompt_block = render_tool_prompt_block(tk)

    assert "concrete decision, clarification, or preference" in description
    assert "materially change the implementation or final result" in description
    assert "ask the user instead of silently guessing" in description
    assert "Do not ask meta-questions" in description
    assert "concrete candidate answers" in description
    assert "not which dimensions" in question_description
    assert "Do not use categories" in options_description
    assert "Multiple plausible approaches or interpretations would materially change the result." in prompt_block
    assert "You only want permission to continue" in prompt_block
