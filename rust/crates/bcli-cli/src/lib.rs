//! `bcli` command-line front end.
//!
//! [`run`] is the whole program minus process plumbing: it takes the argv,
//! an [`Env`] (home dir, cwd, env vars, service URLs, keychain) and an
//! [`Io`] (stdout/stderr sinks), and returns the exit code. `main.rs` wires
//! the real process in; tests wire in temp dirs, mock servers and buffers.

pub mod cli;
mod commands;
mod context;
mod errors;
pub mod output;

use std::ffi::OsString;
use std::io::{self, IsTerminal, Write};
use std::path::PathBuf;

use bcli_core::auth::SecretStore;
use bcli_core::url::ServiceUrls;
use clap::Parser;

use crate::cli::Cli;
use crate::context::Context;

pub type EnvVars = dyn Fn(&str) -> Option<String>;

/// Everything the CLI reads from the outside world.
pub struct Env {
    pub home: PathBuf,
    pub cwd: Option<PathBuf>,
    pub vars: Box<EnvVars>,
    pub urls: ServiceUrls,
    pub secrets: Box<dyn SecretStore>,
}

impl Env {
    pub fn from_process() -> Self {
        let vars: Box<EnvVars> = Box::new(|k| std::env::var(k).ok());
        Self {
            home: home_dir(&*vars),
            cwd: std::env::current_dir().ok(),
            vars,
            urls: ServiceUrls::default(),
            secrets: default_secret_store(),
        }
    }

    pub fn var(&self, key: &str) -> Option<String> {
        (self.vars)(key)
    }
}

/// `pathlib.Path.home()`: `$HOME` on Unix; `%USERPROFILE%` (then
/// `%HOMEDRIVE%%HOMEPATH%`) on Windows.
fn home_dir(vars: &EnvVars) -> PathBuf {
    let found = if cfg!(windows) {
        vars("USERPROFILE").or_else(|| Some(format!("{}{}", vars("HOMEDRIVE")?, vars("HOMEPATH")?)))
    } else {
        vars("HOME")
    };
    found
        .filter(|h| !h.is_empty())
        .map(PathBuf::from)
        .unwrap_or_default()
}

#[cfg(feature = "keyring")]
fn default_secret_store() -> Box<dyn SecretStore> {
    Box::new(bcli_core::auth::OsKeyring)
}

#[cfg(not(feature = "keyring"))]
fn default_secret_store() -> Box<dyn SecretStore> {
    Box::new(bcli_core::auth::NoSecretStore)
}

/// Output sinks plus the terminal facts formatters need.
pub struct Io<'a> {
    pub out: &'a mut dyn Write,
    pub err: &'a mut dyn Write,
    vars: &'a EnvVars,
    pub stdout_is_tty: bool,
    pub stderr_color: bool,
}

impl<'a> Io<'a> {
    pub fn new(out: &'a mut dyn Write, err: &'a mut dyn Write, vars: &'a EnvVars) -> Self {
        Self {
            out,
            err,
            vars,
            stdout_is_tty: false,
            stderr_color: false,
        }
    }

    pub fn var(&self, key: &str) -> Option<String> {
        (self.vars)(key)
    }

    pub fn dim(&self, text: &str) -> String {
        if self.stderr_color {
            format!("\x1b[2m{text}\x1b[0m")
        } else {
            text.to_string()
        }
    }

    pub fn terminal_width(&self) -> usize {
        self.var("COLUMNS")
            .and_then(|c| c.parse().ok())
            .filter(|w| *w > 0)
            .unwrap_or(120)
    }
}

/// Entry point for `main.rs`.
pub fn main_with_process() -> i32 {
    let env = Env::from_process();
    let stdout = io::stdout();
    let stderr = io::stderr();
    let stdout_is_tty = stdout.is_terminal();
    let stderr_color = stderr.is_terminal() && env.var("NO_COLOR").is_none();
    let mut out = stdout.lock();
    let mut err = stderr.lock();
    let mut io = Io::new(&mut out, &mut err, &*env.vars);
    io.stdout_is_tty = stdout_is_tty;
    io.stderr_color = stderr_color;

    let runtime = match tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
    {
        Ok(rt) => rt,
        Err(e) => {
            let _ = writeln!(io.err, "Error: cannot start async runtime: {e}");
            return bcli_core::error::EXIT_GENERIC_ERROR;
        }
    };
    let code = runtime.block_on(run(std::env::args_os().collect(), &env, &mut io));
    let _ = io.out.flush();
    code
}

pub async fn run(args: Vec<OsString>, env: &Env, io: &mut Io<'_>) -> i32 {
    let cli = match Cli::try_parse_from(args) {
        Ok(cli) => cli,
        Err(e) => {
            let rendered = if io.stderr_color {
                e.render().ansi().to_string()
            } else {
                e.render().to_string()
            };
            let sink: &mut dyn Write = if e.use_stderr() {
                &mut *io.err
            } else {
                &mut *io.out
            };
            let _ = write!(sink, "{rendered}");
            return e.exit_code();
        }
    };

    let mut ctx = Context::new(&cli.global, env, io.stdout_is_tty);
    let result = commands::dispatch(&cli.command, &mut ctx, io).await;
    for warning in ctx.take_warnings() {
        let _ = writeln!(io.err, "bcli: warning: {warning}");
    }
    match result {
        Ok(code) => code,
        Err(commands::CmdError::Io(e)) if e.kind() == io::ErrorKind::BrokenPipe => 0,
        Err(commands::CmdError::Io(e)) => {
            let _ = writeln!(io.err, "Error: {e}");
            bcli_core::error::EXIT_GENERIC_ERROR
        }
        Err(commands::CmdError::Bcli(e)) => {
            let message =
                errors::format_for_cli(&e, cli.global.profile.as_deref(), ctx.profile_names());
            let _ = writeln!(io.err, "Error: {message}");
            e.exit_code()
        }
    }
}
