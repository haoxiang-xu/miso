use std::fmt;

use serde::{Deserialize, Serialize};
use thiserror::Error;

const MAX_ID_LENGTH: usize = 128;

#[derive(Clone, Debug, Eq, PartialEq, Error)]
pub enum IdError {
    #[error("identifier must contain between 1 and {MAX_ID_LENGTH} characters")]
    InvalidLength,
    #[error("identifier must contain printable ASCII characters only")]
    InvalidCharacter,
}

fn validate(value: &str) -> Result<(), IdError> {
    if value.is_empty() || value.len() > MAX_ID_LENGTH {
        return Err(IdError::InvalidLength);
    }
    if !value.bytes().all(|byte| (0x20..=0x7e).contains(&byte)) {
        return Err(IdError::InvalidCharacter);
    }
    Ok(())
}

macro_rules! define_id {
    ($name:ident) => {
        #[derive(Clone, Debug, Eq, Hash, PartialEq, Serialize, Deserialize)]
        #[serde(try_from = "String", into = "String")]
        pub struct $name(String);

        impl $name {
            pub fn new(value: impl Into<String>) -> Result<Self, IdError> {
                let value = value.into();
                validate(&value)?;
                Ok(Self(value))
            }

            pub fn as_str(&self) -> &str {
                &self.0
            }
        }

        impl TryFrom<String> for $name {
            type Error = IdError;

            fn try_from(value: String) -> Result<Self, Self::Error> {
                Self::new(value)
            }
        }

        impl From<$name> for String {
            fn from(value: $name) -> Self {
                value.0
            }
        }

        impl fmt::Display for $name {
            fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
                formatter.write_str(&self.0)
            }
        }
    };
}

define_id!(RpcId);
define_id!(RequestId);

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ids_reject_empty_non_ascii_and_oversized_values() {
        assert_eq!(RpcId::new(""), Err(IdError::InvalidLength));
        assert_eq!(RpcId::new("rpc_你"), Err(IdError::InvalidCharacter));
        assert_eq!(
            RequestId::new("x".repeat(MAX_ID_LENGTH + 1)),
            Err(IdError::InvalidLength)
        );
    }

    #[test]
    fn ids_round_trip_as_json_strings() {
        let id = RpcId::new("rpc_01").expect("valid id");
        let encoded = serde_json::to_string(&id).expect("serialize id");
        assert_eq!(encoded, "\"rpc_01\"");
        let decoded: RpcId = serde_json::from_str(&encoded).expect("deserialize id");
        assert_eq!(decoded, id);
    }
}
