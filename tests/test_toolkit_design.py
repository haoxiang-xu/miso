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


def test_tool_can_be_used_as_decorator_with_auto_metadata():
    @Tool
    def summarize(topic: str, max_sentences: int = 2):
        """Summarize topic in brief.

        Args:
            topic: Topic to summarize.
            max_sentences: Maximum number of output sentences.
        """
        return f"{topic}:{max_sentences}"

    schema = summarize.to_json()
    assert summarize.name == "summarize"
    assert summarize.description == "Summarize topic in brief."
    assert schema["parameters"]["required"] == ["topic"]
    assert schema["parameters"]["properties"]["topic"]["description"] == "Topic to summarize."
    assert schema["parameters"]["properties"]["max_sentences"]["description"] == "Maximum number of output sentences."

    result = summarize.execute({"topic": "miso"})
    assert result == {"result": "miso:2"}


def test_tool_can_be_used_as_decorator_factory():
    @Tool()
    def greet(name: str):
        """Return greeting text."""
        return f"hello, {name}"

    schema = greet.to_json()
    assert schema["name"] == "greet"
    assert schema["parameters"]["required"] == ["name"]
    assert greet.execute({"name": "miso"}) == {"result": "hello, miso"}


def test_tool_decorator_factory_supports_custom_name_and_description():
    @Tool(name="rename_tool", description="Renamed tool description.")
    def rename(source: str):
        return source

    schema = rename.to_json()
    assert schema["name"] == "rename_tool"
    assert schema["description"] == "Renamed tool description."
    assert schema["parameters"]["required"] == ["source"]


def test_tool_resolves_string_annotations_from_future_annotations():
    class Demo:
        def run(self, count: int, tags: list[str], enabled: bool = True):
            """Demo function."""
            return {"count": count, "tags": tags, "enabled": enabled}

    demo_tool = Tool.from_callable(Demo().run)
    schema = demo_tool.to_json()

    assert schema["parameters"]["properties"]["count"]["type"] == "integer"
    assert schema["parameters"]["properties"]["tags"]["type"] == "array"
    assert schema["parameters"]["properties"]["enabled"]["type"] == "boolean"
    assert schema["parameters"]["required"] == ["count", "tags"]


def test_toolkit_register_and_unknown_tool_error():
    toolkit_obj = Toolkit()

    def echo(text: str):
        return {"echo": text}

    toolkit_obj.register(echo)
    ok = toolkit_obj.execute("echo", json.dumps({"text": "hello"}))
    assert ok == {"echo": "hello"}

    missing = toolkit_obj.execute("not_exists", {})
    assert "error" in missing


def test_toolkit_register_allows_inline_name_description_overrides():
    toolkit_obj = Toolkit()

    def echo(text: str):
        """Echo text."""
        return {"echo": text}

    registered = toolkit_obj.register(
        echo,
        name="echo_alias",
        description="Custom echo tool.",
    )
    schema = registered.to_json()

    assert schema["name"] == "echo_alias"
    assert schema["description"] == "Custom echo tool."
    assert toolkit_obj.execute("echo_alias", {"text": "ok"}) == {"echo": "ok"}


def test_toolkit_register_many_and_tool_decorator():
    toolkit_obj = Toolkit()

    def add(a: int, b: int):
        return a + b

    def sub(a: int, b: int):
        return a - b

    toolkit_obj.register_many(add, sub)

    @toolkit_obj.tool(observe=True)
    def ping(message: str):
        """Return ping payload."""
        return {"message": message}

    assert "add" in toolkit_obj.tools
    assert "sub" in toolkit_obj.tools
    assert "ping" in toolkit_obj.tools
    assert toolkit_obj.tools["ping"].observe is True
    assert toolkit_obj.execute("add", {"a": 4, "b": 1}) == {"result": 5}
    assert toolkit_obj.execute("sub", {"a": 4, "b": 1}) == {"result": 3}
    assert toolkit_obj.execute("ping", {"message": "pong"}) == {"message": "pong"}
