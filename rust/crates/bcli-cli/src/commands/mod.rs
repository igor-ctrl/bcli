//! Command dispatch. Ported commands have a module; everything else returns
//! a "not yet ported" error (exit 1) so a mixed install fails loudly rather
//! than silently doing less than the Python CLI.

mod company;
mod endpoint;

use std::io;

use bcli_core::BcliError;

use crate::cli::{Command, CompanyCmd, EndpointCmd};
use crate::context::Context;
use crate::Io;

#[derive(Debug)]
pub enum CmdError {
    Bcli(BcliError),
    Io(io::Error),
}

impl From<BcliError> for CmdError {
    fn from(e: BcliError) -> Self {
        CmdError::Bcli(e)
    }
}

impl From<io::Error> for CmdError {
    fn from(e: io::Error) -> Self {
        CmdError::Io(e)
    }
}

/// Exit code on success paths; commands may exit non-zero without an error
/// (e.g. `endpoint info` on an unknown name prints its own message, exit 1).
pub type CmdResult = Result<i32, CmdError>;

pub async fn dispatch(command: &Command, ctx: &mut Context<'_>, io: &mut Io<'_>) -> CmdResult {
    match command {
        Command::Endpoint(EndpointCmd::List {
            custom,
            standard,
            category,
            format,
        }) => endpoint::list(
            ctx,
            io,
            *custom,
            *standard,
            category.as_deref(),
            format.format.as_deref(),
        ),
        Command::Endpoint(EndpointCmd::Search { query }) => endpoint::search(ctx, io, query),
        Command::Endpoint(EndpointCmd::Info { name, format }) => {
            endpoint::info(ctx, io, name, format.format.as_deref())
        }
        Command::Company(CompanyCmd::List(args)) => {
            company::list(ctx, io, args.format.as_deref()).await
        }
        other => Err(BcliError::not_ported(&other.path()).into()),
    }
}

/// `[profile: X | env: Y | company: Z]` on stderr (`print_context_banner`).
pub fn print_context_banner(ctx: &mut Context<'_>, io: &mut Io<'_>) -> Result<(), CmdError> {
    if ctx.quiet {
        return Ok(());
    }
    let profile = ctx.profile()?;
    let mut parts = vec![
        format!("profile: {}", ctx.active_profile_name()?),
        format!("env: {}", profile.environment),
    ];
    match (&profile.company_name, &profile.company_id) {
        (Some(name), _) if !name.is_empty() => parts.push(format!("company: {name}")),
        (_, Some(id)) if !id.is_empty() => parts.push(format!(
            "company: {}...",
            id.chars().take(8).collect::<String>()
        )),
        _ => {}
    }
    writeln!(io.err, "{}", io.dim(&format!("[{}]", parts.join(" | "))))?;
    Ok(())
}
