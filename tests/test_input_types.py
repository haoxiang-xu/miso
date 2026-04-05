from unchain.types.input import InputRequest, InputResponse


def test_input_request_approval():
    req = InputRequest(
        kind="approval",
        run_id="run-1",
        call_id="call-1",
        tool_name="write_file",
        config={"arguments": {"path": "/tmp/test.txt"}, "description": "Write file"},
    )
    assert req.kind == "approval"
    assert req.call_id == "call-1"
    assert req.config["arguments"]["path"] == "/tmp/test.txt"


def test_input_request_question():
    req = InputRequest(
        kind="question",
        run_id="run-1",
        call_id="call-2",
        tool_name="ask_user_question",
        config={
            "title": "Select file",
            "question": "Which?",
            "selection_mode": "single",
            "options": [{"value": "a.txt", "text": "a.txt"}],
        },
    )
    assert req.kind == "question"
    assert req.config["selection_mode"] == "single"


def test_input_request_continue():
    req = InputRequest(
        kind="continue",
        run_id="run-1",
        call_id=None,
        tool_name=None,
        config={"reason": "max_iterations_reached"},
    )
    assert req.call_id is None
    assert req.tool_name is None


def test_input_response_approved():
    resp = InputResponse(decision="approved", response=None)
    assert resp.decision == "approved"


def test_input_response_submitted():
    resp = InputResponse(decision="submitted", response={"selected": ["a.txt"]})
    assert resp.response["selected"] == ["a.txt"]
