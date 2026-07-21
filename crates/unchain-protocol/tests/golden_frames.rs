use unchain_protocol::{ClientMessage, HostMessage, PROTOCOL_MAGIC};

const CLIENT_FRAMES: &str = include_str!(concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/../../protocol/fixtures/v1/client.jsonl"
));
const HOST_FRAMES: &str = include_str!(concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/../../protocol/fixtures/v1/host.jsonl"
));
const FORWARD_CLIENT_FRAMES: &str = include_str!(concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/../../protocol/fixtures/v1/client-forward-compatible.jsonl"
));
const FORWARD_HOST_FRAMES: &str = include_str!(concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/../../protocol/fixtures/v1/host-forward-compatible.jsonl"
));

#[test]
fn version_one_client_golden_frames_remain_decodable() {
    let frames = CLIENT_FRAMES
        .lines()
        .map(|line| serde_json::from_str::<ClientMessage>(line).expect("valid client fixture"))
        .collect::<Vec<_>>();

    assert_eq!(frames.len(), 3);
    for frame in frames {
        let encoded = serde_json::to_value(frame).expect("serialize fixture");
        assert_eq!(encoded["protocol"], PROTOCOL_MAGIC);
    }
}

#[test]
fn version_one_host_golden_frames_remain_decodable() {
    let frames = HOST_FRAMES
        .lines()
        .map(|line| serde_json::from_str::<HostMessage>(line).expect("valid host fixture"))
        .collect::<Vec<_>>();

    assert_eq!(frames.len(), 3);
    for frame in frames {
        let encoded = serde_json::to_value(frame).expect("serialize fixture");
        assert_eq!(encoded["protocol"], PROTOCOL_MAGIC);
    }
}

#[test]
fn newer_minor_optional_fields_are_ignored_by_older_decoders() {
    for line in FORWARD_CLIENT_FRAMES.lines() {
        serde_json::from_str::<ClientMessage>(line).expect("forward-compatible client frame");
    }
    for line in FORWARD_HOST_FRAMES.lines() {
        serde_json::from_str::<HostMessage>(line).expect("forward-compatible host frame");
    }
}
