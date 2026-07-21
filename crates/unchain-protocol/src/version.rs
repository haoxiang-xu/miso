use serde::{Deserialize, Serialize};
use thiserror::Error;

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ProtocolVersion {
    pub major: u16,
    pub minor: u16,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct SupportedProtocol {
    pub major: u16,
    pub min_minor: u16,
    pub max_minor: u16,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Error)]
pub enum NegotiationError {
    #[error("protocol major versions do not overlap")]
    IncompatibleMajor,
    #[error("protocol minor ranges do not overlap")]
    IncompatibleMinor,
    #[error("protocol minor range is invalid")]
    InvalidRange,
}

pub fn negotiate(
    client: SupportedProtocol,
    host: SupportedProtocol,
) -> Result<ProtocolVersion, NegotiationError> {
    if client.min_minor > client.max_minor || host.min_minor > host.max_minor {
        return Err(NegotiationError::InvalidRange);
    }
    if client.major != host.major {
        return Err(NegotiationError::IncompatibleMajor);
    }
    let minimum = client.min_minor.max(host.min_minor);
    let maximum = client.max_minor.min(host.max_minor);
    if minimum > maximum {
        return Err(NegotiationError::IncompatibleMinor);
    }
    Ok(ProtocolVersion {
        major: client.major,
        minor: maximum,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn negotiation_selects_highest_shared_minor() {
        let selected = negotiate(
            SupportedProtocol {
                major: 1,
                min_minor: 0,
                max_minor: 4,
            },
            SupportedProtocol {
                major: 1,
                min_minor: 0,
                max_minor: 2,
            },
        )
        .expect("overlapping versions");
        assert_eq!(selected, ProtocolVersion { major: 1, minor: 2 });
    }

    #[test]
    fn negotiation_rejects_incompatible_versions() {
        assert_eq!(
            negotiate(
                SupportedProtocol {
                    major: 2,
                    min_minor: 0,
                    max_minor: 0,
                },
                SupportedProtocol {
                    major: 1,
                    min_minor: 0,
                    max_minor: 0,
                },
            ),
            Err(NegotiationError::IncompatibleMajor)
        );
    }
}
