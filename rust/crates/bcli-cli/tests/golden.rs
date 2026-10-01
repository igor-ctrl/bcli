//! The Rust binary must reproduce the Python CLI's recorded output exactly
//! (stdout, stderr and exit code) for every case in `fixtures/golden/cases.txt`.
//! Regenerate the recordings with `rust/scripts/parity.sh`.

use std::path::{Path, PathBuf};

use assert_cmd::Command;

fn fixtures() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests")
        .join("fixtures")
}

fn read(path: &Path) -> String {
    std::fs::read_to_string(path)
        .unwrap_or_else(|e| panic!("{}: {e}", path.display()))
        .replace("\r\n", "\n")
}

#[test]
fn matches_python_golden_outputs() {
    let golden = fixtures().join("golden");
    let home = fixtures().join("home");
    let cases = read(&golden.join("cases.txt"));
    let mut failures = Vec::new();
    let mut count = 0;

    for line in cases
        .lines()
        .filter(|l| !l.trim().is_empty() && !l.starts_with('#'))
    {
        let (name, args) = line.split_once('|').expect("case line is `name | args`");
        let (name, args) = (name.trim(), args.split_whitespace().collect::<Vec<_>>());
        count += 1;

        let output = Command::cargo_bin("bcli")
            .unwrap()
            .args(&args)
            .env_clear()
            .env("HOME", &home)
            .env("USERPROFILE", &home)
            .current_dir(&home)
            .output()
            .unwrap();

        let actual_out = String::from_utf8_lossy(&output.stdout).replace("\r\n", "\n");
        let actual_err = String::from_utf8_lossy(&output.stderr).replace("\r\n", "\n");
        let actual_code = output.status.code().unwrap_or(-1).to_string();
        let expect = |ext: &str| read(&golden.join(format!("{name}.{ext}")));

        // CSV is CRLF on both sides; compare it byte-for-byte separately.
        if args.contains(&"csv") {
            let raw = std::fs::read(golden.join(format!("{name}.out"))).unwrap();
            if raw != output.stdout {
                failures.push(format!("{name}: csv bytes differ"));
            }
        }
        for (what, expected, actual) in [
            ("stdout", expect("out"), actual_out),
            ("stderr", expect("err"), actual_err),
            ("exit code", expect("code").trim().to_string(), actual_code),
        ] {
            if expected != actual {
                failures.push(format!(
                    "{name}: {what} differs\n--- python\n{expected}\n--- rust\n{actual}"
                ));
            }
        }
    }

    assert!(count >= 10, "expected the golden cases to be discovered");
    assert!(failures.is_empty(), "{}", failures.join("\n\n"));
}
