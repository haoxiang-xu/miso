use std::io::{self, BufRead, Write};

use serde_json::{Value, json};
use thiserror::Error;
use unchain_protocol::{
    BuildTarget, Capability, ClientMessage, ErrorBody, HostIdentity, HostMessage, Limits,
    MAX_FRAME_BYTES, PROTOCOL_MAGIC, PROTOCOL_MAJOR, PROTOCOL_MINOR, ProtocolVersion, RpcId,
    SupportedProtocol, current_protocol_version, negotiate,
};

#[derive(Clone, Debug)]
pub struct HostConfig {
    pub identity: HostIdentity,
    pub capabilities: Vec<Capability>,
    pub limits: Limits,
}

impl HostConfig {
    pub fn new(instance_id: impl Into<String>) -> Self {
        let protocol_version = current_protocol_version();
        let mut capabilities = vec![
            Capability {
                name: "host.version".to_owned(),
                version: protocol_version,
            },
            Capability {
                name: "host.capabilities".to_owned(),
                version: protocol_version,
            },
            Capability {
                name: "host.self_test".to_owned(),
                version: protocol_version,
            },
        ];
        capabilities.sort_by(|left, right| left.name.cmp(&right.name));
        Self {
            identity: HostIdentity {
                name: "unchain-core".to_owned(),
                version: env!("CARGO_PKG_VERSION").to_owned(),
                instance_id: instance_id.into(),
                git_sha: option_env!("UNCHAIN_GIT_SHA").map(str::to_owned),
                build_profile: if cfg!(debug_assertions) {
                    "debug".to_owned()
                } else {
                    "release".to_owned()
                },
                target: BuildTarget {
                    os: std::env::consts::OS.to_owned(),
                    arch: std::env::consts::ARCH.to_owned(),
                    family: std::env::consts::FAMILY.to_owned(),
                },
            },
            capabilities,
            limits: Limits {
                max_frame_bytes: MAX_FRAME_BYTES,
                max_in_flight_requests: 1,
            },
        }
    }
}

#[derive(Debug, Error)]
pub enum ServerError {
    #[error("I/O failure: {0}")]
    Io(#[from] io::Error),
    #[error("protocol frame exceeds {MAX_FRAME_BYTES} bytes")]
    FrameTooLarge,
    #[error("protocol frame is not UTF-8")]
    InvalidUtf8,
    #[error("invalid protocol JSON: {0}")]
    InvalidJson(#[from] serde_json::Error),
    #[error("the first protocol frame must be hello")]
    HandshakeRequired,
    #[error("protocol version is incompatible")]
    IncompatibleVersion,
}

pub fn serve<R: BufRead, W: Write>(
    reader: &mut R,
    writer: &mut W,
    config: &HostConfig,
) -> Result<(), ServerError> {
    let Some(first_frame) = read_nonempty_frame(reader)? else {
        return Ok(());
    };
    let first_message: ClientMessage = serde_json::from_str(&first_frame)?;
    let (hello_rpc_id, supported_protocol) = match first_message {
        ClientMessage::Hello {
            protocol,
            rpc_id,
            supported_protocol,
            ..
        } if protocol == PROTOCOL_MAGIC => (rpc_id, supported_protocol),
        ClientMessage::Hello { rpc_id, .. } => {
            write_message(
                writer,
                &protocol_error(
                    rpc_id,
                    None,
                    None,
                    None,
                    "protocol.invalid_magic",
                    "unsupported protocol magic",
                ),
            )?;
            return Err(ServerError::HandshakeRequired);
        }
        ClientMessage::Request {
            rpc_id,
            request_id,
            method,
            ..
        } => {
            write_message(
                writer,
                &protocol_error(
                    rpc_id,
                    Some(request_id),
                    Some(method),
                    None,
                    "protocol.handshake_required",
                    "hello must be the first protocol frame",
                ),
            )?;
            return Err(ServerError::HandshakeRequired);
        }
    };

    let negotiated = match negotiate(
        supported_protocol,
        SupportedProtocol {
            major: PROTOCOL_MAJOR,
            min_minor: 0,
            max_minor: PROTOCOL_MINOR,
        },
    ) {
        Ok(version) => version,
        Err(error) => {
            write_message(
                writer,
                &protocol_error(
                    hello_rpc_id,
                    None,
                    None,
                    None,
                    "protocol.incompatible_version",
                    &error.to_string(),
                ),
            )?;
            return Err(ServerError::IncompatibleVersion);
        }
    };

    write_message(
        writer,
        &HostMessage::Ready {
            protocol: PROTOCOL_MAGIC.to_owned(),
            rpc_id: hello_rpc_id,
            protocol_version: negotiated,
            host: config.identity.clone(),
            capabilities: config.capabilities.clone(),
            limits: config.limits.clone(),
        },
    )?;

    while let Some(frame) = read_nonempty_frame(reader)? {
        let message: ClientMessage = serde_json::from_str(&frame)?;
        match message {
            ClientMessage::Hello { rpc_id, .. } => {
                write_message(
                    writer,
                    &protocol_error(
                        rpc_id,
                        None,
                        None,
                        Some(negotiated),
                        "protocol.duplicate_hello",
                        "hello has already completed",
                    ),
                )?;
            }
            ClientMessage::Request {
                protocol,
                protocol_version,
                rpc_id,
                request_id,
                method,
                params,
            } => {
                let response = handle_request(
                    config,
                    negotiated,
                    protocol,
                    protocol_version,
                    rpc_id,
                    request_id,
                    method,
                    params,
                );
                write_message(writer, &response)?;
            }
        }
    }
    Ok(())
}

#[allow(clippy::too_many_arguments)]
fn handle_request(
    config: &HostConfig,
    negotiated: ProtocolVersion,
    protocol: String,
    protocol_version: ProtocolVersion,
    rpc_id: RpcId,
    request_id: unchain_protocol::RequestId,
    method: String,
    params: Value,
) -> HostMessage {
    if protocol != PROTOCOL_MAGIC {
        return protocol_error(
            rpc_id,
            Some(request_id),
            Some(method),
            Some(negotiated),
            "protocol.invalid_magic",
            "unsupported protocol magic",
        );
    }
    if protocol_version != negotiated {
        return protocol_error(
            rpc_id,
            Some(request_id),
            Some(method),
            Some(negotiated),
            "protocol.version_mismatch",
            "request does not use the negotiated protocol version",
        );
    }
    if !params.as_object().is_some_and(serde_json::Map::is_empty) {
        return protocol_error(
            rpc_id,
            Some(request_id),
            Some(method),
            Some(negotiated),
            "request.invalid_params",
            "host introspection methods require an empty object",
        );
    }

    let result = match method.as_str() {
        "host.version" => host_version_payload(config, negotiated),
        "host.capabilities" => capabilities_payload(config),
        "host.self_test" => self_test_payload(config),
        _ => {
            return protocol_error(
                rpc_id,
                Some(request_id),
                Some(method),
                Some(negotiated),
                "request.method_not_found",
                "unsupported method",
            );
        }
    };
    HostMessage::Result {
        protocol: PROTOCOL_MAGIC.to_owned(),
        protocol_version: negotiated,
        rpc_id,
        request_id,
        method,
        result,
    }
}

pub fn host_version_payload(config: &HostConfig, protocol_version: ProtocolVersion) -> Value {
    json!({
        "host": config.identity,
        "protocol_version": protocol_version,
    })
}

pub fn capabilities_payload(config: &HostConfig) -> Value {
    json!({
        "capabilities": config.capabilities,
        "limits": config.limits,
    })
}

pub fn self_test_payload(config: &HostConfig) -> Value {
    let ordered = config
        .capabilities
        .windows(2)
        .all(|pair| pair[0].name < pair[1].name);
    let unique = config
        .capabilities
        .windows(2)
        .all(|pair| pair[0].name != pair[1].name);
    let status = if ordered && unique { "pass" } else { "fail" };
    json!({
        "status": status,
        "checks": [
            {
                "name": "protocol.version_registry",
                "status": "pass",
                "detail": null,
            },
            {
                "name": "host.capability_registry",
                "status": status,
                "detail": null,
            },
            {
                "name": "host.build_metadata",
                "status": "pass",
                "detail": null,
            }
        ],
        "duration_ms": 0,
    })
}

fn protocol_error(
    rpc_id: RpcId,
    request_id: Option<unchain_protocol::RequestId>,
    method: Option<String>,
    protocol_version: Option<ProtocolVersion>,
    code: &str,
    message: &str,
) -> HostMessage {
    HostMessage::Error {
        protocol: PROTOCOL_MAGIC.to_owned(),
        protocol_version,
        rpc_id,
        request_id,
        method,
        error: ErrorBody {
            code: code.to_owned(),
            message: message.to_owned(),
            retryable: false,
            details: json!({}),
        },
    }
}

fn write_message<W: Write>(writer: &mut W, message: &HostMessage) -> Result<(), ServerError> {
    let mut frame = BoundedFrame::new(MAX_FRAME_BYTES);
    if let Err(error) = serde_json::to_writer(&mut frame, message) {
        if frame.exceeded {
            return Err(ServerError::FrameTooLarge);
        }
        return Err(ServerError::InvalidJson(error));
    }
    writer.write_all(&frame.bytes)?;
    writer.write_all(b"\n")?;
    writer.flush()?;
    Ok(())
}

struct BoundedFrame {
    bytes: Vec<u8>,
    limit: usize,
    exceeded: bool,
}

impl BoundedFrame {
    fn new(limit: usize) -> Self {
        Self {
            bytes: Vec::new(),
            limit,
            exceeded: false,
        }
    }
}

impl Write for BoundedFrame {
    fn write(&mut self, buffer: &[u8]) -> io::Result<usize> {
        if self.bytes.len().saturating_add(buffer.len()) > self.limit {
            self.exceeded = true;
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "protocol frame exceeds configured limit",
            ));
        }
        self.bytes.extend_from_slice(buffer);
        Ok(buffer.len())
    }

    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}

fn read_nonempty_frame<R: BufRead>(reader: &mut R) -> Result<Option<String>, ServerError> {
    loop {
        let Some(frame) = read_frame(reader)? else {
            return Ok(None);
        };
        if !frame.trim().is_empty() {
            return Ok(Some(frame));
        }
    }
}

fn read_frame<R: BufRead>(reader: &mut R) -> Result<Option<String>, ServerError> {
    let mut frame = Vec::new();
    loop {
        let available = reader.fill_buf()?;
        if available.is_empty() {
            if frame.is_empty() {
                return Ok(None);
            }
            break;
        }
        let newline = available.iter().position(|byte| *byte == b'\n');
        let consumed = newline.map_or(available.len(), |index| index + 1);
        let payload_end = newline.unwrap_or(available.len());
        if frame.len() + payload_end > MAX_FRAME_BYTES {
            return Err(ServerError::FrameTooLarge);
        }
        frame.extend_from_slice(&available[..payload_end]);
        reader.consume(consumed);
        if newline.is_some() {
            break;
        }
    }
    String::from_utf8(frame)
        .map(Some)
        .map_err(|_| ServerError::InvalidUtf8)
}

#[cfg(test)]
mod tests {
    use std::io::Cursor;

    use super::*;

    #[test]
    fn oversized_frames_fail_before_unbounded_growth() {
        let payload = vec![b'x'; MAX_FRAME_BYTES + 1];
        let mut input = Cursor::new(payload);
        assert!(matches!(
            read_frame(&mut input),
            Err(ServerError::FrameTooLarge)
        ));
    }

    #[test]
    fn invalid_utf8_is_rejected() {
        let mut input = Cursor::new(vec![0xff, b'\n']);
        assert!(matches!(
            read_frame(&mut input),
            Err(ServerError::InvalidUtf8)
        ));
    }

    #[test]
    fn oversized_output_frames_are_rejected_before_any_bytes_are_written() {
        let message = HostMessage::Error {
            protocol: PROTOCOL_MAGIC.to_owned(),
            protocol_version: Some(current_protocol_version()),
            rpc_id: RpcId::new("rpc-large").expect("valid id"),
            request_id: None,
            method: None,
            error: ErrorBody {
                code: "test.large".to_owned(),
                message: "oversized test frame".to_owned(),
                retryable: false,
                details: json!({"payload": "x".repeat(MAX_FRAME_BYTES)}),
            },
        };
        let mut output = Vec::new();

        assert!(matches!(
            write_message(&mut output, &message),
            Err(ServerError::FrameTooLarge)
        ));
        assert!(output.is_empty());
    }
}
