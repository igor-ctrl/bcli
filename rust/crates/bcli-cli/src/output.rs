//! Record formatters (`bcli_cli.output._formatters`).
//!
//! `json`, `ndjson`, `raw`, `markdown`, `records` and `csv` reproduce the
//! Python output byte-for-byte for JSON-typed values. `table` is a
//! comfy-table rendering, not a pixel copy of rich: humans read it, agents
//! get markdown or JSON.

use std::io;

use bcli_core::client::Record;
use bcli_core::pyjson;
use comfy_table::{presets, ColumnConstraint, ContentArrangement, Table, Width};
use serde_json::Value;

use crate::Io;

const TABLE_MAX_COLS: usize = 12;
const MARKDOWN_MAX_WIDTH: usize = 60;

/// `detect_default_format`: env pin, agent markers, non-TTY, legacy Windows console.
pub fn detect_default_format(var: &dyn Fn(&str) -> Option<String>, stdout_is_tty: bool) -> String {
    if let Some(fmt) = var("BCLI_FORMAT").filter(|v| !v.is_empty()) {
        return fmt;
    }
    let set = |k: &str| var(k).is_some_and(|v| !v.is_empty());
    if set("CLAUDECODE") || set("BCLI_AGENT") {
        return "markdown".into();
    }
    if !stdout_is_tty {
        return "json".into();
    }
    if cfg!(windows) && !set("WT_SESSION") {
        return "markdown".into();
    }
    "table".into()
}

pub fn format_output(records: &[Record], fmt: &str, io: &mut Io<'_>) -> io::Result<()> {
    if records.is_empty() {
        return writeln!(io.err, "{}", io.dim("No records found."));
    }
    let fmt = if matches!(fmt, "table" | "markdown" | "md") && should_auto_records(records, io) {
        "records"
    } else {
        fmt
    };
    match fmt {
        "markdown" | "md" => markdown(records, io),
        "records" | "record" | "r" | "vertical" => vertical(records, io),
        "json" => writeln!(io.out, "{}", pyjson::dumps(&to_array(records), Some(2))),
        "csv" => csv_out(records, io),
        "ndjson" => records.iter().try_for_each(|r| {
            writeln!(io.out, "{}", pyjson::dumps(&Value::Object(r.clone()), None))
        }),
        "raw" => {
            let mut wrapper = Record::new();
            wrapper.insert("value".into(), to_array(records));
            writeln!(
                io.out,
                "{}",
                pyjson::dumps(&Value::Object(wrapper), Some(2))
            )
        }
        _ => table(records, io),
    }
}

fn to_array(records: &[Record]) -> Value {
    Value::Array(records.iter().cloned().map(Value::Object).collect())
}

fn columns(records: &[Record]) -> Vec<&str> {
    records[0]
        .keys()
        .map(String::as_str)
        .filter(|k| !k.starts_with("@odata"))
        .collect()
}

/// Flip 1–2 wide rows to the vertical view (`_should_auto_records`).
fn should_auto_records(records: &[Record], io: &Io<'_>) -> bool {
    if io
        .var("BCLI_NO_AUTO_RECORDS")
        .is_some_and(|v| !v.is_empty())
        || records.len() > 2
    {
        return false;
    }
    let cols = columns(records);
    if cols.len() <= 6 {
        return false;
    }
    if cols.len() > 8 {
        return true;
    }
    let estimated: usize = cols
        .iter()
        .map(|c| {
            c.chars()
                .count()
                .max(cell(records[0].get(*c)).chars().count())
                + 3
        })
        .sum();
    estimated > io.terminal_width()
}

/// `_format_cell`: `None` → "", bools lower-case, containers as compact JSON.
pub fn cell(value: Option<&Value>) -> String {
    match value {
        None | Some(Value::Null) => String::new(),
        Some(Value::Bool(b)) => b.to_string(),
        Some(Value::String(s)) => s.clone(),
        Some(Value::Number(n)) => pyjson::number_repr(n),
        Some(v @ (Value::Array(_) | Value::Object(_))) => pyjson::dumps(v, None),
    }
}

fn markdown_cell(value: Option<&Value>) -> String {
    cell(value)
        .replace('|', "\\|")
        .replace('\n', " ")
        .replace('\r', "")
}

fn pad(cell: &str, width: usize) -> String {
    let len = cell.chars().count();
    if len > width {
        let mut s: String = cell.chars().take(width.saturating_sub(1)).collect();
        s.push('…');
        s
    } else {
        format!("{cell}{}", " ".repeat(width - len))
    }
}

fn markdown(records: &[Record], io: &mut Io<'_>) -> io::Result<()> {
    let cols = columns(records);
    let rows: Vec<Vec<String>> = records
        .iter()
        .map(|r| cols.iter().map(|c| markdown_cell(r.get(*c))).collect())
        .collect();
    let mut widths: Vec<usize> = cols.iter().map(|c| c.chars().count()).collect();
    for row in &rows {
        for (w, c) in widths.iter_mut().zip(row) {
            let len = c.chars().count();
            if len > *w {
                *w = len.min(MARKDOWN_MAX_WIDTH);
            }
        }
    }
    let line = |cells: Vec<String>| format!("| {} |", cells.join(" | "));
    writeln!(
        io.out,
        "{}",
        line(cols.iter().zip(&widths).map(|(c, w)| pad(c, *w)).collect())
    )?;
    writeln!(
        io.out,
        "{}",
        line(widths.iter().map(|w| "-".repeat(*w)).collect())
    )?;
    for row in &rows {
        writeln!(
            io.out,
            "{}",
            line(row.iter().zip(&widths).map(|(c, w)| pad(c, *w)).collect())
        )?;
    }
    record_count(records.len(), io)
}

fn vertical(records: &[Record], io: &mut Io<'_>) -> io::Result<()> {
    let cols = columns(records);
    if cols.is_empty() {
        return Ok(());
    }
    let label_width = cols
        .iter()
        .map(|c| c.chars().count())
        .max()
        .unwrap_or(0)
        .min(40);
    let multi = records.len() > 1;
    for (idx, record) in records.iter().enumerate() {
        if multi {
            writeln!(io.out, "record {}", idx + 1)?;
        }
        for col in &cols {
            let value = cell(record.get(*col));
            let mut lines = split_lines(&value).into_iter();
            let first = lines.next().unwrap_or_default();
            let label = format!("{col:<label_width$}");
            writeln!(io.out, "  {label} : {first}")?;
            for extra in lines {
                writeln!(io.out, "  {}   {extra}", " ".repeat(label_width))?;
            }
        }
        if idx + 1 != records.len() {
            writeln!(io.out)?;
        }
    }
    record_count(records.len(), io)
}

/// `str.splitlines()` for the line breaks BC data actually contains.
fn split_lines(s: &str) -> Vec<&str> {
    let mut out = Vec::new();
    let mut rest = s;
    while !rest.is_empty() {
        match rest.find(['\n', '\r']) {
            Some(i) => {
                out.push(&rest[..i]);
                let skip = if rest[i..].starts_with("\r\n") { 2 } else { 1 };
                rest = &rest[i + skip..];
            }
            None => {
                out.push(rest);
                break;
            }
        }
    }
    out
}

/// Python's `csv` writes `str(value)`, and `""` for None.
fn csv_cell(value: Option<&Value>) -> String {
    match value {
        None | Some(Value::Null) => String::new(),
        Some(Value::String(s)) => s.clone(),
        Some(other) => pyjson::py_repr(other),
    }
}

fn csv_out(records: &[Record], io: &mut Io<'_>) -> io::Result<()> {
    let cols = columns(records);
    let mut writer = csv::WriterBuilder::new()
        .terminator(csv::Terminator::CRLF)
        .from_writer(Vec::new());
    let to_io = |e: csv::Error| io::Error::other(e.to_string());
    writer.write_record(&cols).map_err(to_io)?;
    for r in records {
        writer
            .write_record(cols.iter().map(|c| csv_cell(r.get(*c))))
            .map_err(to_io)?;
    }
    let bytes = writer
        .into_inner()
        .map_err(|e| io::Error::other(e.to_string()))?;
    io.out.write_all(&bytes)
}

fn table(records: &[Record], io: &mut Io<'_>) -> io::Result<()> {
    let cols = columns(records);
    let truncated = cols.len() > TABLE_MAX_COLS;
    let shown = &cols[..cols.len().min(TABLE_MAX_COLS)];
    let mut header: Vec<String> = shown.iter().map(|c| c.to_string()).collect();
    if truncated {
        header.push("...".into());
    }
    let mut t = new_table(header);
    for (i, column) in t.column_iter_mut().enumerate() {
        if i > 0 {
            column.set_constraint(ColumnConstraint::UpperBoundary(Width::Fixed(40)));
        }
    }
    for r in records {
        let mut row: Vec<String> = shown.iter().map(|c| cell(r.get(*c))).collect();
        if truncated {
            row.push(format!("+{} cols", cols.len() - TABLE_MAX_COLS));
        }
        t.add_row(row);
    }
    writeln!(io.out, "{t}")?;
    record_count(records.len(), io)
}

pub fn new_table(header: Vec<String>) -> Table {
    let mut t = Table::new();
    t.load_preset(presets::UTF8_FULL_CONDENSED)
        .set_content_arrangement(ContentArrangement::Dynamic)
        .set_header(header);
    t
}

fn record_count(n: usize, io: &mut Io<'_>) -> io::Result<()> {
    writeln!(io.err, "{}", io.dim(&format!("{n} record(s)")))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn rec(v: Value) -> Record {
        match v {
            Value::Object(o) => o,
            _ => unreachable!(),
        }
    }

    #[test]
    fn default_format_detection() {
        let none = |_: &str| None;
        assert_eq!(detect_default_format(&none, false), "json");
        let agent = |k: &str| (k == "CLAUDECODE").then(|| "1".to_string());
        assert_eq!(detect_default_format(&agent, true), "markdown");
        let pinned = |k: &str| (k == "BCLI_FORMAT").then(|| "csv".to_string());
        assert_eq!(detect_default_format(&pinned, false), "csv");
    }

    #[test]
    fn cells_match_python() {
        assert_eq!(cell(Some(&json!(true))), "true");
        assert_eq!(cell(Some(&json!(null))), "");
        assert_eq!(cell(Some(&json!(2.0))), "2.0");
        assert_eq!(cell(Some(&json!({"a": [1]}))), r#"{"a": [1]}"#);
        assert_eq!(markdown_cell(Some(&json!("a|b\nc"))), r"a\|b c");
        assert_eq!(csv_cell(Some(&json!(false))), "False");
    }

    #[test]
    fn markdown_pads_and_truncates() {
        assert_eq!(pad("abc", 5), "abc  ");
        assert_eq!(pad("abcdef", 4), "abc…");
    }

    #[test]
    fn split_lines_handles_crlf() {
        assert_eq!(split_lines("a\r\nb\nc\rd"), ["a", "b", "c", "d"]);
        assert!(split_lines("").is_empty());
    }

    #[test]
    fn csv_uses_crlf_and_minimal_quoting() {
        let mut out = Vec::new();
        let mut err = Vec::new();
        let vars = |_: &str| None;
        let mut io = Io::new(&mut out, &mut err, &vars);
        let rows = [rec(json!({"a": "x,y", "b": true, "@odata.etag": "e"}))];
        csv_out(&rows, &mut io).unwrap();
        assert_eq!(String::from_utf8(out).unwrap(), "a,b\r\n\"x,y\",True\r\n");
    }
}
