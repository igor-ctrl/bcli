//! `bcli endpoint list|search|info` (`bcli_cli.commands.endpoint_cmd`).

use std::io::Write;

use bcli_core::client::Record;
use bcli_core::pyjson;
use bcli_core::registry::{EndpointMetadata, Registry};
use comfy_table::{ColumnConstraint, Width};
use serde_json::{json, Value};

use super::CmdResult;
use crate::context::Context;
use crate::output::{format_output, new_table};
use crate::Io;

/// Stable JSON shape for an endpoint — consumed by bcli-mcp.
fn endpoint_to_record(ep: &EndpointMetadata) -> Record {
    let Value::Object(record) = json!({
        "name": ep.entity_set_name,
        "category": ep.category,
        "custom": ep.is_custom(),
        "supported_ops": ep.supports,
        "key_field": ep.key_field,
        "publisher": ep.api_publisher,
        "group": ep.api_group,
        "version": ep.api_version,
        "description": ep.description,
        "caution": ep.caution.as_str(),
    }) else {
        unreachable!("json! object literal")
    };
    record
}

pub fn list(
    ctx: &mut Context<'_>,
    io: &mut Io<'_>,
    custom: bool,
    standard: bool,
    category: Option<&str>,
    format: Option<&str>,
) -> CmdResult {
    let registry = ctx.registry()?;
    let mut endpoints = registry.list_all(custom, standard);
    if let Some(category) = category {
        let wanted = category.to_lowercase();
        endpoints.retain(|e| e.category.to_lowercase() == wanted);
    }

    let fmt = format.unwrap_or(&ctx.format).to_string();
    if fmt != "table" {
        let rows: Vec<Record> = endpoints.iter().map(|e| endpoint_to_record(e)).collect();
        format_output(&rows, &fmt, io)?;
        return Ok(0);
    }

    let mut table = new_table(
        ["Entity", "Route", "Operations", "Category", "Description"]
            .map(String::from)
            .to_vec(),
    );
    if let Some(col) = table.column_mut(4) {
        col.set_constraint(ColumnConstraint::UpperBoundary(Width::Fixed(50)));
    }
    for ep in &endpoints {
        table.add_row(vec![
            ep.entity_set_name.clone(),
            ep.route_display(),
            ep.supports.join(", "),
            ep.category.clone(),
            ep.description.chars().take(50).collect(),
        ]);
    }
    writeln!(io.out, "{table}")?;
    writeln!(
        io.out,
        "{} endpoint(s) ({} standard, {} custom)",
        endpoints.len(),
        registry.standard_count(),
        registry.custom_count()
    )?;
    Ok(0)
}

pub fn search(ctx: &mut Context<'_>, io: &mut Io<'_>, query: &str) -> CmdResult {
    let registry = ctx.registry()?;
    let results = registry.search(query);
    if results.is_empty() {
        writeln!(io.out, "No endpoints matching '{query}'")?;
        return Ok(0);
    }
    let mut table = new_table(
        ["Entity", "Route", "Description"]
            .map(String::from)
            .to_vec(),
    );
    if let Some(col) = table.column_mut(2) {
        col.set_constraint(ColumnConstraint::UpperBoundary(Width::Fixed(60)));
    }
    for ep in results.iter().take(20) {
        table.add_row(vec![
            ep.entity_set_name.clone(),
            ep.route_display(),
            ep.description.clone(),
        ]);
    }
    writeln!(io.out, "{table}")?;
    Ok(0)
}

pub fn info(ctx: &mut Context<'_>, io: &mut Io<'_>, name: &str, format: Option<&str>) -> CmdResult {
    let registry = ctx.registry()?;
    let Some(ep) = registry.get(name) else {
        return not_found(&registry, io, name);
    };

    if format.unwrap_or(&ctx.format) == "json" {
        let mut payload = endpoint_to_record(ep);
        payload.insert("entity_name".into(), json!(ep.entity_name));
        payload.insert(
            "fields".into(),
            Value::Array(
                ep.field_names
                    .iter()
                    .map(|f| json!({"name": f, "type": ""}))
                    .collect(),
            ),
        );
        payload.insert(
            "fields_discovered".into(),
            json!(!ep.field_names.is_empty()),
        );
        payload.insert("source_table".into(), json!(ep.source_table));
        payload.insert("page_number".into(), json!(ep.page_number));
        writeln!(
            io.out,
            "{}",
            pyjson::dumps(&Value::Object(payload), Some(2))
        )?;
        return Ok(0);
    }

    let out = &mut io.out;
    writeln!(out, "{}", ep.entity_set_name)?;
    writeln!(out, "  Entity name:  {}", ep.entity_name)?;
    writeln!(out, "  Route:        {}", ep.route_display())?;
    writeln!(out, "  Operations:   {}", ep.supports.join(", "))?;
    writeln!(out, "  Key field:    {}", ep.key_field)?;
    writeln!(out, "  Category:     {}", ep.category)?;
    writeln!(out, "  Caution:      {}", ep.caution.as_str())?;
    writeln!(
        out,
        "  Custom:       {}",
        if ep.is_custom() {
            "Yes"
        } else {
            "No (standard v2.0)"
        }
    )?;
    for (label, value) in [
        ("Description: ", &ep.description),
        ("Source table:", &ep.source_table),
        ("Page number: ", &ep.page_number),
    ] {
        if !value.is_empty() {
            writeln!(out, "  {label} {value}")?;
        }
    }
    if !ep.field_names.is_empty() {
        writeln!(out, "  Fields:       {}", ep.field_names.join(", "))?;
    }
    Ok(0)
}

fn not_found(registry: &Registry, io: &mut Io<'_>, name: &str) -> CmdResult {
    writeln!(io.err, "Endpoint '{name}' not found.")?;
    let suggestions: Vec<&str> = registry
        .search(name)
        .into_iter()
        .take(3)
        .map(|m| m.entity_set_name.as_str())
        .collect();
    if !suggestions.is_empty() {
        writeln!(
            io.err,
            "{}",
            io.dim(&format!("Did you mean: {}?", suggestions.join(", ")))
        )?;
    }
    Ok(1)
}
