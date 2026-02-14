import json

from miso import LLM_tool, LLM_toolkit


def test_tool_parameter_inference_and_execute():
    def add(a: int, b: int = 2):
        return a + b

    tool = LLM_tool.from_callable(add, observe=True)

    schema = tool.to_json()
    assert schema["name"] == "add"
    assert schema["parameters"]["properties"]["a"]["type"] == "integer"
    assert schema["parameters"]["properties"]["b"]["type"] == "integer"
    assert schema["parameters"]["required"] == ["a"]
    assert tool.observe is True

    result = tool.execute('{"a": 5}')
    assert result == {"result": 7}


def test_toolkit_register_and_unknown_tool_error():
    toolkit = LLM_toolkit()

    def echo(text: str):
        return {"echo": text}

    toolkit.register(echo)
    ok = toolkit.execute("echo", json.dumps({"text": "hello"}))
    assert ok == {"echo": "hello"}

    missing = toolkit.execute("not_exists", {})
    assert "error" in missing
