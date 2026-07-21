#![forbid(unsafe_code)]

mod ids;
mod messages;
mod version;

pub use ids::{IdError, RequestId, RpcId};
pub use messages::{
    BuildTarget, Capability, ClientIdentity, ClientMessage, ErrorBody, HostIdentity, HostMessage,
    Limits,
};
pub use version::{NegotiationError, ProtocolVersion, SupportedProtocol, negotiate};

pub const PROTOCOL_MAGIC: &str = "unchain.host";
pub const PROTOCOL_MAJOR: u16 = 1;
pub const PROTOCOL_MINOR: u16 = 0;
pub const MAX_FRAME_BYTES: usize = 1_048_576;

pub fn current_protocol_version() -> ProtocolVersion {
    ProtocolVersion {
        major: PROTOCOL_MAJOR,
        minor: PROTOCOL_MINOR,
    }
}
