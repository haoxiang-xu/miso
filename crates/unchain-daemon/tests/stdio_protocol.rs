use std::io::{Cursor, Write};
use std::process::{Command, Stdio};

use serde_json::{Value, json};
use unchain_daemon::{HostConfig, ServerError, serve};

fn jsonl(messages: &[Value]) -> Vec<u8> {
    let mut payload = messages
        .iter()
        .map(Value::to_string)
        .collect::<Vec<_>>()
        .join("\n");
    payload.push('\n');
    payload.into_bytes()
}

fn output_messages(output: &[u8]) -> Vec<Value> {
    String::from_utf8(output.to_vec())
        .expect("protocol output is UTF-8")
        .lines()
        .map(|line| serde_json::from_str(line).expect("protocol output is JSON"))
        .collect()
}

#[test]
fn stdio_session_negotiates_and_survives_a_request_error() {
    let input = jsonl(&[
        json!({
            "type": "hello",
            "protocol": "unchain.host",
            "rpc_id": "rpc-hello",
            "client": {"name": "python-test", "version": "0.2.0"},
            "supported_protocol": {"major": 1, "min_minor": 0, "max_minor": 0}
        }),
        json!({
            "type": "request",
            "protocol": "unchain.host",
            "protocol_version": {"major": 1, "minor": 0},
            "rpc_id": "rpc-missing",
            "request_id": "req-missing",
            "method": "host.missing",
            "params": {}
        }),
        json!({
            "type": "request",
            "protocol": "unchain.host",
            "protocol_version": {"major": 1, "minor": 0},
            "rpc_id": "rpc-self-test",
            "request_id": "req-self-test",
            "method": "host.self_test",
            "params": {}
        }),
    ]);
    let mut reader = Cursor::new(input);
    let mut output = Vec::new();

    serve(&mut reader, &mut output, &HostConfig::new("host-test"))
        .expect("recoverable request errors keep the session alive");

    let messages = output_messages(&output);
    assert_eq!(messages.len(), 3);
    assert_eq!(messages[0]["type"], "ready");
    assert_eq!(messages[0]["rpc_id"], "rpc-hello");
    assert_eq!(
        messages[0]["protocol_version"],
        json!({"major": 1, "minor": 0})
    );
    assert_eq!(messages[1]["type"], "error");
    assert_eq!(messages[1]["error"]["code"], "request.method_not_found");
    assert_eq!(messages[1]["request_id"], "req-missing");
    assert_eq!(messages[2]["type"], "result");
    assert_eq!(messages[2]["request_id"], "req-self-test");
    assert_eq!(messages[2]["result"]["status"], "pass");
}

#[test]
fn request_before_hello_is_correlated_and_rejected() {
    let input = jsonl(&[json!({
        "type": "request",
        "protocol": "unchain.host",
        "protocol_version": {"major": 1, "minor": 0},
        "rpc_id": "rpc-early",
        "request_id": "req-early",
        "method": "host.version",
        "params": {}
    })]);
    let mut reader = Cursor::new(input);
    let mut output = Vec::new();

    let error = serve(&mut reader, &mut output, &HostConfig::new("host-test"))
        .expect_err("requests require a completed handshake");

    assert!(matches!(error, ServerError::HandshakeRequired));
    let messages = output_messages(&output);
    assert_eq!(messages.len(), 1);
    assert_eq!(messages[0]["type"], "error");
    assert_eq!(messages[0]["rpc_id"], "rpc-early");
    assert_eq!(messages[0]["request_id"], "req-early");
    assert_eq!(messages[0]["error"]["code"], "protocol.handshake_required");
}

#[test]
fn binary_reports_machine_readable_health() {
    let binary = env!("CARGO_BIN_EXE_unchain-core");
    let version = Command::new(binary)
        .arg("--version-json")
        .output()
        .expect("run version command");
    assert!(version.status.success());
    let version: Value = serde_json::from_slice(&version.stdout).expect("version JSON");
    assert_eq!(version["host"]["name"], "unchain-core");
    assert_eq!(version["protocol_version"], json!({"major": 1, "minor": 0}));

    let self_test = Command::new(binary)
        .arg("--self-test-json")
        .output()
        .expect("run self-test command");
    assert!(self_test.status.success());
    let self_test: Value = serde_json::from_slice(&self_test.stdout).expect("self-test JSON");
    assert_eq!(self_test["status"], "pass");
}

#[test]
fn binary_stdio_session_uses_clean_jsonl_until_eof() {
    let input = jsonl(&[
        json!({
            "type": "hello",
            "protocol": "unchain.host",
            "rpc_id": "rpc-hello",
            "client": {"name": "python-test", "version": "0.2.0"},
            "supported_protocol": {"major": 1, "min_minor": 0, "max_minor": 0}
        }),
        json!({
            "type": "request",
            "protocol": "unchain.host",
            "protocol_version": {"major": 1, "minor": 0},
            "rpc_id": "rpc-capabilities",
            "request_id": "req-capabilities",
            "method": "host.capabilities",
            "params": {}
        }),
    ]);
    let mut child = Command::new(env!("CARGO_BIN_EXE_unchain-core"))
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .expect("spawn protocol host");
    child
        .stdin
        .take()
        .expect("child stdin")
        .write_all(&input)
        .expect("write protocol session");

    let output = child.wait_with_output().expect("wait for protocol host");
    assert!(output.status.success());
    assert!(
        output.stderr.is_empty(),
        "stderr must contain diagnostics only"
    );
    let messages = output_messages(&output.stdout);
    assert_eq!(messages.len(), 2);
    assert_eq!(messages[0]["type"], "ready");
    assert_eq!(messages[1]["type"], "result");
    assert_eq!(messages[1]["method"], "host.capabilities");
}

#[test]
fn binary_reports_a_broken_output_pipe_without_panicking() {
    let input = jsonl(&[json!({
        "type": "hello",
        "protocol": "unchain.host",
        "rpc_id": "rpc-broken-pipe",
        "client": {"name": "python-test", "version": "0.2.0"},
        "supported_protocol": {"major": 1, "min_minor": 0, "max_minor": 0}
    })]);
    let mut child = Command::new(env!("CARGO_BIN_EXE_unchain-core"))
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .expect("spawn protocol host");
    drop(child.stdout.take());
    child
        .stdin
        .take()
        .expect("child stdin")
        .write_all(&input)
        .expect("write protocol session");

    let output = child.wait_with_output().expect("wait for protocol host");
    assert_eq!(output.status.code(), Some(2));
    let stderr = String::from_utf8(output.stderr).expect("diagnostics are UTF-8");
    assert!(stderr.contains("unchain-core protocol error:"));
    assert!(!stderr.to_ascii_lowercase().contains("panicked"));
}
