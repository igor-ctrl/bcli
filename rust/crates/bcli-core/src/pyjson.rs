//! JSON serialization byte-compatible with Python's `json.dumps`.
//!
//! Agents and scripts diff bcli output, and `tokens.json` is shared with the
//! Python build, so the Rust port reproduces `json.dumps` defaults exactly:
//! `ensure_ascii=True` (non-ASCII escaped as `\uXXXX`), `", "` / `": "`
//! separators in compact mode, and `repr()`-style floats (`1e+16`, `1e-05`).
//! Key order is preserved (`serde_json/preserve_order`).

use std::io;

use serde::Serialize;
use serde_json::ser::{CharEscape, Formatter, Serializer};
use serde_json::Value;

/// `json.dumps(value)` (compact) or `json.dumps(value, indent=n)`.
pub fn dumps(value: &Value, indent: Option<usize>) -> String {
    let mut buf = Vec::new();
    let formatter = PyFormatter::new(indent);
    let mut ser = Serializer::with_formatter(&mut buf, formatter);
    value
        .serialize(&mut ser)
        .expect("serializing a serde_json::Value into memory cannot fail");
    String::from_utf8(buf).expect("formatter only emits ASCII")
}

/// Python `repr()` of a JSON number (`str(1.0) == "1.0"`, `str(1e16) == "1e+16"`).
pub fn number_repr(n: &serde_json::Number) -> String {
    if let Some(f) = n.as_f64().filter(|_| n.is_f64()) {
        float_repr(f)
    } else {
        n.to_string()
    }
}

/// Python `repr()` of a JSON-decoded value (`['GET', 'PATCH']`, `{'a': None}`),
/// which is what `csv` writes for nested values.
pub fn py_repr(value: &Value) -> String {
    match value {
        Value::Null => "None".into(),
        Value::Bool(true) => "True".into(),
        Value::Bool(false) => "False".into(),
        Value::Number(n) => number_repr(n),
        Value::String(s) => str_repr(s),
        Value::Array(items) => {
            let inner: Vec<String> = items.iter().map(py_repr).collect();
            format!("[{}]", inner.join(", "))
        }
        Value::Object(map) => {
            let inner: Vec<String> = map
                .iter()
                .map(|(k, v)| format!("{}: {}", str_repr(k), py_repr(v)))
                .collect();
            format!("{{{}}}", inner.join(", "))
        }
    }
}

fn str_repr(s: &str) -> String {
    let quote = if s.contains('\'') && !s.contains('"') {
        '"'
    } else {
        '\''
    };
    let mut out = String::with_capacity(s.len() + 2);
    out.push(quote);
    for ch in s.chars() {
        match ch {
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if c == quote => {
                out.push('\\');
                out.push(c);
            }
            c if c.is_control() || (c.is_whitespace() && c != ' ') => {
                let cp = c as u32;
                if cp <= 0xff {
                    out.push_str(&format!("\\x{cp:02x}"));
                } else if cp <= 0xffff {
                    out.push_str(&format!("\\u{cp:04x}"));
                } else {
                    out.push_str(&format!("\\U{cp:08x}"));
                }
            }
            c => out.push(c),
        }
    }
    out.push(quote);
    out
}

pub fn float_repr(value: f64) -> String {
    if value.is_nan() {
        return "NaN".into();
    }
    if value.is_infinite() {
        return if value > 0.0 {
            "Infinity".into()
        } else {
            "-Infinity".into()
        };
    }
    // Rust's `{:?}` already picks the shortest round-trip digits and switches
    // to exponent form at the same thresholds as Python (<1e-4, >=1e16); only
    // the exponent spelling differs.
    let s = format!("{value:?}");
    match s.split_once('e') {
        Some((mantissa, exp)) => {
            let (sign, digits) = match exp.strip_prefix('-') {
                Some(d) => ('-', d),
                None => ('+', exp),
            };
            format!("{mantissa}e{sign}{digits:0>2}")
        }
        None => s,
    }
}

struct PyFormatter {
    indent: Option<usize>,
    depth: usize,
    has_value: bool,
}

impl PyFormatter {
    fn new(indent: Option<usize>) -> Self {
        Self {
            indent,
            depth: 0,
            has_value: false,
        }
    }

    fn newline_indent<W: ?Sized + io::Write>(&self, w: &mut W) -> io::Result<()> {
        if let Some(n) = self.indent {
            w.write_all(b"\n")?;
            for _ in 0..self.depth * n {
                w.write_all(b" ")?;
            }
        }
        Ok(())
    }

    fn separator<W: ?Sized + io::Write>(&self, w: &mut W, first: bool) -> io::Result<()> {
        match (self.indent, first) {
            (Some(_), true) => self.newline_indent(w),
            (Some(_), false) => {
                w.write_all(b",")?;
                self.newline_indent(w)
            }
            (None, true) => Ok(()),
            (None, false) => w.write_all(b", "),
        }
    }

    fn open<W: ?Sized + io::Write>(&mut self, w: &mut W, bracket: &[u8]) -> io::Result<()> {
        self.depth += 1;
        self.has_value = false;
        w.write_all(bracket)
    }

    fn close<W: ?Sized + io::Write>(&mut self, w: &mut W, bracket: &[u8]) -> io::Result<()> {
        self.depth -= 1;
        if self.has_value {
            self.newline_indent(w)?;
        }
        w.write_all(bracket)
    }
}

impl Formatter for PyFormatter {
    fn write_f64<W: ?Sized + io::Write>(&mut self, w: &mut W, value: f64) -> io::Result<()> {
        w.write_all(float_repr(value).as_bytes())
    }

    fn write_f32<W: ?Sized + io::Write>(&mut self, w: &mut W, value: f32) -> io::Result<()> {
        self.write_f64(w, f64::from(value))
    }

    fn write_string_fragment<W: ?Sized + io::Write>(
        &mut self,
        w: &mut W,
        fragment: &str,
    ) -> io::Result<()> {
        for ch in fragment.chars() {
            let cp = ch as u32;
            if (0x20..0x7f).contains(&cp) {
                w.write_all(&[cp as u8])?;
            } else {
                let mut units = [0u16; 2];
                for unit in ch.encode_utf16(&mut units) {
                    write!(w, "\\u{unit:04x}")?;
                }
            }
        }
        Ok(())
    }

    fn write_char_escape<W: ?Sized + io::Write>(
        &mut self,
        w: &mut W,
        escape: CharEscape,
    ) -> io::Result<()> {
        let s: &[u8] = match escape {
            CharEscape::Quote => b"\\\"",
            CharEscape::ReverseSolidus => b"\\\\",
            CharEscape::Solidus => b"/",
            CharEscape::Backspace => b"\\b",
            CharEscape::FormFeed => b"\\f",
            CharEscape::LineFeed => b"\\n",
            CharEscape::CarriageReturn => b"\\r",
            CharEscape::Tab => b"\\t",
            CharEscape::AsciiControl(byte) => {
                return write!(w, "\\u{:04x}", byte);
            }
        };
        w.write_all(s)
    }

    fn begin_array<W: ?Sized + io::Write>(&mut self, w: &mut W) -> io::Result<()> {
        self.open(w, b"[")
    }

    fn end_array<W: ?Sized + io::Write>(&mut self, w: &mut W) -> io::Result<()> {
        self.close(w, b"]")
    }

    fn begin_array_value<W: ?Sized + io::Write>(
        &mut self,
        w: &mut W,
        first: bool,
    ) -> io::Result<()> {
        self.separator(w, first)
    }

    fn end_array_value<W: ?Sized + io::Write>(&mut self, _w: &mut W) -> io::Result<()> {
        self.has_value = true;
        Ok(())
    }

    fn begin_object<W: ?Sized + io::Write>(&mut self, w: &mut W) -> io::Result<()> {
        self.open(w, b"{")
    }

    fn end_object<W: ?Sized + io::Write>(&mut self, w: &mut W) -> io::Result<()> {
        self.close(w, b"}")
    }

    fn begin_object_key<W: ?Sized + io::Write>(
        &mut self,
        w: &mut W,
        first: bool,
    ) -> io::Result<()> {
        self.separator(w, first)
    }

    fn begin_object_value<W: ?Sized + io::Write>(&mut self, w: &mut W) -> io::Result<()> {
        w.write_all(b": ")
    }

    fn end_object_value<W: ?Sized + io::Write>(&mut self, _w: &mut W) -> io::Result<()> {
        self.has_value = true;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn compact_uses_python_separators() {
        assert_eq!(
            dumps(&json!({"a": 1, "b": [1, 2]}), None),
            r#"{"a": 1, "b": [1, 2]}"#
        );
    }

    #[test]
    fn pretty_matches_indent_2() {
        let v = json!({"a": [], "b": {}, "c": [1, {"d": null}]});
        let expected = "{\n  \"a\": [],\n  \"b\": {},\n  \"c\": [\n    1,\n    {\n      \"d\": null\n    }\n  ]\n}";
        assert_eq!(dumps(&v, Some(2)), expected);
    }

    #[test]
    fn ensure_ascii_escapes_non_ascii_and_del() {
        assert_eq!(
            dumps(&json!("Müller ✈ 😀\u{7f}"), None),
            r#""M\u00fcller \u2708 \ud83d\ude00\u007f""#
        );
        assert_eq!(
            dumps(&json!("a\"b\\c\n\u{1}/"), None),
            r#""a\"b\\c\n\u0001/""#
        );
    }

    #[test]
    fn repr_matches_python() {
        let v = json!({"a": [1, 2.5, null, true], "b": "it's", "c": "x\ty\u{a0}é"});
        assert_eq!(
            py_repr(&v),
            r#"{'a': [1, 2.5, None, True], 'b': "it's", 'c': 'x\ty\xa0é'}"#
        );
        assert_eq!(py_repr(&json!("both ' and \"")), r#"'both \' and "'"#);
    }

    #[test]
    fn floats_use_python_repr() {
        assert_eq!(float_repr(1.0), "1.0");
        assert_eq!(float_repr(0.1), "0.1");
        assert_eq!(float_repr(1e16), "1e+16");
        assert_eq!(float_repr(1e15), "1000000000000000.0");
        assert_eq!(float_repr(0.0001), "0.0001");
        assert_eq!(float_repr(0.00001), "1e-05");
        assert_eq!(float_repr(1.5e300), "1.5e+300");
        assert_eq!(float_repr(-2.5e-7), "-2.5e-07");
    }
}
