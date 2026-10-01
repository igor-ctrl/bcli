//! `bcli company list` (`bcli_cli.commands.company_cmd`).

use bcli_core::client::Record;
use bcli_core::Result;
use serde_json::{json, Value};

use super::{print_context_banner, CmdResult};
use crate::context::Context;
use crate::output::{format_output, new_table};
use crate::Io;

/// JSON shape: `[{"id", "name", "alias" (string|null), "is_default"}]`.
/// Stable contract — consumed by the bcli-mcp `bcli_company_list` tool.
pub async fn list(ctx: &mut Context<'_>, io: &mut Io<'_>, format: Option<&str>) -> CmdResult {
    let fmt = format.unwrap_or(&ctx.format).to_string();
    if matches!(fmt.as_str(), "json" | "csv" | "ndjson" | "markdown" | "md") {
        ctx.quiet = true;
    }
    print_context_banner(ctx, io)?;

    // The Python command reports any failure here as `Error: …` on stdout
    // with exit 1, bypassing the exit-code taxonomy. Kept for parity.
    let rows = match fetch_rows(ctx).await {
        Ok(rows) => rows,
        Err(e) => {
            writeln!(io.out, "Error: {e}")?;
            return Ok(1);
        }
    };

    if fmt != "table" {
        format_output(&rows, &fmt, io)?;
        return Ok(0);
    }

    let mut table = new_table(
        ["#", "Alias", "Company Name", "Company ID"]
            .map(String::from)
            .to_vec(),
    );
    for (i, row) in rows.iter().enumerate() {
        let is_default = row["is_default"].as_bool().unwrap_or(false);
        let mut name = row["name"].as_str().unwrap_or_default().to_string();
        let mut alias = row["alias"].as_str().unwrap_or_default().to_string();
        if is_default {
            name.push_str(" ◄");
            if alias.is_empty() {
                alias = "default".into();
            }
        }
        table.add_row(vec![
            (i + 1).to_string(),
            alias,
            name,
            row["id"].as_str().unwrap_or_default().to_string(),
        ]);
    }
    writeln!(io.out, "{table}")?;
    writeln!(io.out, "{} company(ies)", rows.len())?;
    if ctx.profile()?.companies.is_empty() {
        writeln!(
            io.out,
            "\nTip: assign nicknames with 'bcli company alias <name> <company-id>'"
        )?;
    }
    Ok(0)
}

async fn fetch_rows(ctx: &mut Context<'_>) -> Result<Vec<Record>> {
    let mut client = ctx.client()?;
    let companies = client.list_companies().await?;
    let profile = ctx.profile()?;
    let default_id = profile.company_id.clone().unwrap_or_default();

    Ok(companies
        .iter()
        .map(|company| {
            let id = company
                .get("id")
                .and_then(Value::as_str)
                .unwrap_or_default();
            let alias = profile
                .companies
                .iter()
                .rev()
                .find(|(_, c)| c.id == id)
                .map(|(alias, _)| alias.clone())
                .filter(|a| !a.is_empty());
            let Value::Object(row) = json!({
                "id": id,
                "name": company.get("name").and_then(Value::as_str).unwrap_or_default(),
                "alias": alias,
                "is_default": !default_id.is_empty() && id == default_id,
            }) else {
                unreachable!("json! object literal")
            };
            row
        })
        .collect())
}
