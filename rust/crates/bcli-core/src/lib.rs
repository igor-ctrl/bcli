//! Core library for the Rust port of bcli.
//!
//! Mirrors the Python `bcli` package (the SDK half of the project): config
//! loading, the endpoint registry, URL construction, auth, and the HTTP
//! transport. Everything that touches the process environment (home
//! directory, env vars, cwd) takes it as an explicit argument so the CLI and
//! the tests can inject it.

pub mod auth;
pub mod client;
pub mod config;
pub mod error;
pub mod http;
pub mod paths;
pub mod pyjson;
pub mod registry;
pub mod url;

pub use error::{BcliError, ErrorKind, Result};

/// Version reported by `bcli --version`; kept in lock-step with `pyproject.toml`.
pub const VERSION: &str = env!("CARGO_PKG_VERSION");
