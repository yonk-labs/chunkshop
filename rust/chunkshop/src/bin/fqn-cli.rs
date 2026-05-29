//! Thin CLI exposing `build_fqn` + `code_symbol_node_id` for cross-port
//! testing. Invoked by `python/tests/chunkshop/test_rust_cross_port_parity.py`.
//!
//! Usage:
//!   fqn-cli build-fqn <file_path> <symbol_name> [parent_name]
//!   fqn-cli node-id <project_id> <language> <file_path> <fqn>
//!
//! Stdout: the computed string, no trailing newline (use print! not println!).
//! Stderr: error message + exit 1 on bad usage.

use chunkshop::codeparse::{build_fqn, code_symbol_node_id};
use std::process::exit;

fn main() {
    let args: Vec<String> = std::env::args().collect();
    if args.len() < 2 {
        eprintln!("usage: {} <build-fqn|node-id> <args...>", args[0]);
        exit(1);
    }
    match args[1].as_str() {
        "build-fqn" => match &args[2..] {
            [file_path, symbol_name] => {
                print!("{}", build_fqn(file_path, symbol_name, None));
            }
            [file_path, symbol_name, parent_name] => {
                let parent = if parent_name.is_empty() {
                    None
                } else {
                    Some(parent_name.as_str())
                };
                print!("{}", build_fqn(file_path, symbol_name, parent));
            }
            _ => {
                eprintln!("usage: build-fqn <file_path> <symbol_name> [parent_name]");
                exit(1);
            }
        },
        "node-id" => match &args[2..] {
            [project_id, language, file_path, fqn] => {
                print!(
                    "{}",
                    code_symbol_node_id(project_id, language, file_path, fqn)
                );
            }
            _ => {
                eprintln!("usage: node-id <project_id> <language> <file_path> <fqn>");
                exit(1);
            }
        },
        other => {
            eprintln!("unknown subcommand: {other}");
            exit(1);
        }
    }
}
