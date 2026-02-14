import json

from miso import tool as Tool, toolkit as Toolkit


def test_tool_parameter_inference_and_execute():
    def add(a: int, b: int = 2):
        return a + b

    tool_obj = Tool.from_callable(add, observe=True)

    schema = tool_obj.to_json()
    assert schema["name"] == "add"
    assert schema["parameters"]["properties"]["a"]["type"] == "integer"
    assert schema["parameters"]["properties"]["b"]["type"] == "integer"
    assert schema["parameters"]["required"] == ["a"]
    assert tool_obj.observe is True

    result = tool_obj.execute('{"a": 5}')
    assert result == {"result": 7}


def test_toolkit_register_and_unknown_tool_error():
    toolkit_obj = Toolkit()

    def echo(text: str):
        return {"echo": text}

    toolkit_obj.register(echo)
    ok = toolkit_obj.execute("echo", json.dumps({"text": "hello"}))
    assert ok == {"echo": "hello"}

    missing = toolkit_obj.execute("not_exists", {})
    assert "error" in missing
