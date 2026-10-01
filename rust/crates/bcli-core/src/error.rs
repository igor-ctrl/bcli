//! Error type and exit-code taxonomy (`bcli.errors` + `bcli.exit_codes`).

use std::fmt;

pub type Result<T, E = BcliError> = std::result::Result<T, E>;

pub const EXIT_OK: i32 = 0;
pub const EXIT_GENERIC_ERROR: i32 = 1;
pub const EXIT_USAGE: i32 = 2;
pub const EXIT_AUTH: i32 = 3;
pub const EXIT_NOT_FOUND: i32 = 4;
pub const EXIT_VALIDATION: i32 = 5;
pub const EXIT_REMOTE_4XX: i32 = 6;
pub const EXIT_REMOTE_5XX: i32 = 7;
pub const EXIT_POLICY: i32 = 8;

/// Ordered taxonomy consumed by `bcli describe`. Labels are a public contract.
pub const EXIT_CODES: [(i32, &str); 9] = [
    (EXIT_OK, "success"),
    (EXIT_GENERIC_ERROR, "uncategorised error"),
    (EXIT_USAGE, "usage error"),
    (EXIT_AUTH, "authentication failure"),
    (EXIT_NOT_FOUND, "not found"),
    (EXIT_VALIDATION, "input validation"),
    (EXIT_REMOTE_4XX, "remote 4xx"),
    (EXIT_REMOTE_5XX, "remote 5xx"),
    (EXIT_POLICY, "policy violation"),
];

pub fn exit_code_for_status(status: Option<u16>) -> i32 {
    match status {
        Some(400..=499) => EXIT_REMOTE_4XX,
        Some(500..=599) => EXIT_REMOTE_5XX,
        _ => EXIT_GENERIC_ERROR,
    }
}

/// One variant per Python exception subclass of `BCLIError`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ErrorKind {
    Generic,
    Auth,
    Forbidden,
    NotFound,
    Validation,
    Throttled,
    Server,
    Config,
    Registry,
    Safety,
    /// Command exists in the Python CLI but has no Rust handler yet.
    NotPorted,
}

#[derive(Debug, Clone)]
pub struct BcliError {
    pub kind: ErrorKind,
    pub message: String,
    pub status_code: Option<u16>,
    pub bc_message: Option<String>,
    pub correlation_id: Option<String>,
    pub retry_after: Option<f64>,
}

impl BcliError {
    pub fn new(kind: ErrorKind, message: impl Into<String>) -> Self {
        Self {
            kind,
            message: message.into(),
            status_code: None,
            bc_message: None,
            correlation_id: None,
            retry_after: None,
        }
    }

    pub fn config(message: impl Into<String>) -> Self {
        Self::new(ErrorKind::Config, message)
    }

    pub fn auth(message: impl Into<String>) -> Self {
        Self::new(ErrorKind::Auth, message)
    }

    pub fn registry(message: impl Into<String>) -> Self {
        Self::new(ErrorKind::Registry, message)
    }

    pub fn server(message: impl Into<String>) -> Self {
        Self::new(ErrorKind::Server, message)
    }

    pub fn validation(message: impl Into<String>) -> Self {
        Self::new(ErrorKind::Validation, message)
    }

    pub fn not_ported(command: &str) -> Self {
        Self::new(
            ErrorKind::NotPorted,
            format!(
                "'bcli {command}' is not yet ported to the Rust build. \
                 Use the Python bcli (`uv tool install bc-cli`) for this command."
            ),
        )
    }

    /// Error class for an HTTP status, matching `_ERROR_MAP` in the transport.
    pub fn kind_for_status(status: u16) -> ErrorKind {
        match status {
            400 => ErrorKind::Validation,
            401 => ErrorKind::Auth,
            403 => ErrorKind::Forbidden,
            404 => ErrorKind::NotFound,
            429 => ErrorKind::Throttled,
            500 | 502 | 503 | 504 => ErrorKind::Server,
            _ => ErrorKind::Generic,
        }
    }

    pub fn with_status(mut self, status: u16) -> Self {
        self.status_code = Some(status);
        self
    }

    pub fn with_bc_message(mut self, bc_message: Option<String>) -> Self {
        self.bc_message = bc_message;
        self
    }

    pub fn with_correlation_id(mut self, correlation_id: Option<String>) -> Self {
        self.correlation_id = correlation_id;
        self
    }

    /// Documented CLI exit code (`map_error_to_exit_code`).
    pub fn exit_code(&self) -> i32 {
        match self.kind {
            ErrorKind::Auth | ErrorKind::Forbidden => EXIT_AUTH,
            ErrorKind::NotFound | ErrorKind::Registry => EXIT_NOT_FOUND,
            ErrorKind::Validation => EXIT_VALIDATION,
            ErrorKind::Config => EXIT_USAGE,
            ErrorKind::Safety => EXIT_POLICY,
            ErrorKind::NotPorted => EXIT_GENERIC_ERROR,
            ErrorKind::Generic | ErrorKind::Throttled | ErrorKind::Server => {
                exit_code_for_status(self.status_code)
            }
        }
    }
}

impl fmt::Display for BcliError {
    /// `message | BC says: … | Correlation ID: …`, as `BCLIError.__init__` renders it.
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.message)?;
        if let Some(bc) = self.bc_message.as_deref() {
            if !bc.is_empty() && bc != self.message {
                write!(f, " | BC says: {bc}")?;
            }
        }
        if let Some(cid) = self.correlation_id.as_deref() {
            if !cid.is_empty() {
                write!(f, " | Correlation ID: {cid}")?;
            }
        }
        Ok(())
    }
}

impl std::error::Error for BcliError {}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn exit_codes_follow_the_python_taxonomy() {
        assert_eq!(BcliError::auth("x").exit_code(), EXIT_AUTH);
        assert_eq!(
            BcliError::new(ErrorKind::Forbidden, "x").exit_code(),
            EXIT_AUTH
        );
        assert_eq!(BcliError::registry("x").exit_code(), EXIT_NOT_FOUND);
        assert_eq!(BcliError::config("x").exit_code(), EXIT_USAGE);
        assert_eq!(BcliError::validation("x").exit_code(), EXIT_VALIDATION);
        assert_eq!(
            BcliError::new(ErrorKind::Safety, "x").exit_code(),
            EXIT_POLICY
        );
        assert_eq!(
            BcliError::server("x").with_status(503).exit_code(),
            EXIT_REMOTE_5XX
        );
        assert_eq!(BcliError::server("x").exit_code(), EXIT_GENERIC_ERROR);
        assert_eq!(
            BcliError::new(ErrorKind::Generic, "x")
                .with_status(409)
                .exit_code(),
            EXIT_REMOTE_4XX
        );
    }

    #[test]
    fn display_appends_bc_message_and_correlation_id() {
        let err = BcliError::validation("HTTP 400 Bad Request: GET u")
            .with_bc_message(Some("bad filter".into()))
            .with_correlation_id(Some("abc".into()));
        assert_eq!(
            err.to_string(),
            "HTTP 400 Bad Request: GET u | BC says: bad filter | Correlation ID: abc"
        );
    }

    #[test]
    fn display_skips_bc_message_identical_to_message() {
        let err = BcliError::validation("same").with_bc_message(Some("same".into()));
        assert_eq!(err.to_string(), "same");
    }
}
