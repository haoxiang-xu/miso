from unchain.toolkits import AskUserToolkit
from unchain.input import ASK_USER_QUESTION_TOOL_NAME


def test_ask_user_question_description_encourages_asking_when_multiple_paths_exist():
    tk = AskUserToolkit()

    tool_json = tk.to_json()[0]
    description = tool_json["description"]
    question_description = tool_json["parameters"]["properties"]["question"]["description"]
    options_description = tool_json["parameters"]["properties"]["options"]["description"]

    assert "Strongly prefer this" in description
    assert "multiple plausible approaches" in description
    assert "ask the user instead of silently guessing" in description
    assert "Do not ask meta-questions" in description
    assert "concrete candidate answers" in description
    assert "not which dimensions" in question_description
    assert "Do not use categories" in options_description
