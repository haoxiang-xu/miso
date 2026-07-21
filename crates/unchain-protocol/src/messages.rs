use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::{ProtocolVersion, RequestId, RpcId, SupportedProtocol};

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ClientIdentity {
    pub name: String,
    pub version: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct BuildTarget {
    pub os: String,
    pub arch: String,
    pub family: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct HostIdentity {
    pub name: String,
    pub version: String,
    pub instance_id: String,
    pub git_sha: Option<String>,
    pub build_profile: String,
    pub target: BuildTarget,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct Capability {
    pub name: String,
    pub version: ProtocolVersion,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct Limits {
    pub max_frame_bytes: usize,
    pub max_in_flight_requests: usize,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ErrorBody {
    pub code: String,
    pub message: String,
    pub retryable: bool,
    #[serde(default)]
    pub details: Value,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum ClientMessage {
    Hello {
        protocol: String,
        rpc_id: RpcId,
        client: ClientIdentity,
        supported_protocol: SupportedProtocol,
    },
    Request {
        protocol: String,
        protocol_version: ProtocolVersion,
        rpc_id: RpcId,
        request_id: RequestId,
        method: String,
        #[serde(default)]
        params: Value,
    },
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum HostMessage {
    Ready {
        protocol: String,
        rpc_id: RpcId,
        protocol_version: ProtocolVersion,
        host: HostIdentity,
        capabilities: Vec<Capability>,
        limits: Limits,
    },
    Result {
        protocol: String,
        protocol_version: ProtocolVersion,
        rpc_id: RpcId,
        request_id: RequestId,
        method: String,
        result: Value,
    },
    Error {
        protocol: String,
        protocol_version: Option<ProtocolVersion>,
        rpc_id: RpcId,
        request_id: Option<RequestId>,
        method: Option<String>,
        error: ErrorBody,
    },
}
