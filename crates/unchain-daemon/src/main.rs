#![forbid(unsafe_code)]

use std::io;
use std::process::ExitCode;

use clap::Parser;
use unchain_daemon::{HostConfig, host_version_payload, self_test_payload, serve};
use unchain_protocol::current_protocol_version;
use uuid::Uuid;

#[derive(Debug, Parser)]
#[command(name = "unchain-core")]
#[command(about = "Native Unchain execution host")]
struct Args {
    #[arg(long, default_value = "stdio")]
    transport: String,
    #[arg(long, conflicts_with = "self_test_json")]
    version_json: bool,
    #[arg(long, conflicts_with = "version_json")]
    self_test_json: bool,
}

fn main() -> ExitCode {
    let args = Args::parse();
    let config = HostConfig::new(format!("host_{}", Uuid::new_v4().simple()));

    if args.version_json {
        println!(
            "{}",
            host_version_payload(&config, current_protocol_version())
        );
        return ExitCode::SUCCESS;
    }
    if args.self_test_json {
        let payload = self_test_payload(&config);
        println!("{payload}");
        return if payload["status"] == "pass" {
            ExitCode::SUCCESS
        } else {
            ExitCode::from(1)
        };
    }
    if args.transport != "stdio" {
        eprintln!("unsupported transport: {}", args.transport);
        return ExitCode::from(2);
    }

    let stdin = io::stdin();
    let stdout = io::stdout();
    match serve(&mut stdin.lock(), &mut stdout.lock(), &config) {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("unchain-core protocol error: {error}");
            ExitCode::from(2)
        }
    }
}
