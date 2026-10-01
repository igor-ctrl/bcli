//! The clap tree must declare the same commands, positionals and options as
//! the Python Typer app. `fixtures/python-cli-surface.json` is produced by
//! `rust/scripts/dump_python_surface.py`; refresh it when the Python CLI
//! changes and this test will list what the Rust tree is missing.

use std::collections::BTreeSet;

use bcli_cli::cli::Cli;
use clap::{ArgAction, CommandFactory};
use serde_json::Value;

/// Typer's shell-completion installers; the Rust build will ship
/// `clap_complete`-generated scripts instead.
const INTENTIONALLY_OMITTED: [&str; 2] = ["--install-completion", "--show-completion"];

#[derive(Debug, PartialEq, Eq, PartialOrd, Ord)]
struct Opt {
    long: Vec<String>,
    short: Vec<String>,
    flag: bool,
    hidden: bool,
    multiple: bool,
    required: bool,
}

#[derive(Debug, PartialEq, Eq)]
struct Positional {
    name: String,
    required: bool,
    variadic: bool,
}

fn strings(v: &Value) -> Vec<String> {
    let mut out: Vec<String> = v
        .as_array()
        .unwrap()
        .iter()
        .map(|s| s.as_str().unwrap().to_string())
        .collect();
    out.sort();
    out
}

fn python_options(entry: &Value) -> BTreeSet<Opt> {
    entry["options"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|o| {
            !strings(&o["long"])
                .iter()
                .any(|l| INTENTIONALLY_OMITTED.contains(&l.as_str()))
        })
        .map(|o| Opt {
            long: strings(&o["long"]),
            short: strings(&o["short"]),
            flag: o["flag"].as_bool().unwrap(),
            hidden: o["hidden"].as_bool().unwrap(),
            multiple: o["multiple"].as_bool().unwrap(),
            required: o["required"].as_bool().unwrap(),
        })
        .collect()
}

fn rust_options(cmd: &clap::Command) -> BTreeSet<Opt> {
    cmd.get_arguments()
        .filter(|a| !a.is_positional() && a.get_id() != "help")
        .map(|a| {
            let mut long: Vec<String> = a
                .get_long()
                .into_iter()
                .chain(a.get_all_aliases().unwrap_or_default())
                .map(|l| format!("--{l}"))
                .collect();
            long.sort();
            Opt {
                long,
                short: a
                    .get_short()
                    .map(|s| vec![format!("-{s}")])
                    .unwrap_or_default(),
                flag: !a.get_action().takes_values(),
                hidden: a.is_hide_set(),
                multiple: matches!(a.get_action(), ArgAction::Append),
                required: a.is_required_set(),
            }
        })
        .collect()
}

fn python_positionals(entry: &Value) -> Vec<Positional> {
    entry["positionals"]
        .as_array()
        .unwrap()
        .iter()
        .map(|p| Positional {
            name: p["name"].as_str().unwrap().to_string(),
            required: p["required"].as_bool().unwrap(),
            variadic: p["nargs"].as_i64() == Some(-1),
        })
        .collect()
}

fn rust_positionals(cmd: &clap::Command) -> Vec<Positional> {
    cmd.get_positionals()
        .map(|a| Positional {
            name: a.get_id().to_string(),
            required: a.is_required_set(),
            variadic: a.get_num_args().is_some_and(|n| n.max_values() > 1),
        })
        .collect()
}

fn find<'a>(root: &'a clap::Command, path: &[String]) -> Option<&'a clap::Command> {
    path.iter()
        .try_fold(root, |cmd, name| cmd.find_subcommand(name))
}

fn rust_paths(cmd: &clap::Command, prefix: Vec<String>, out: &mut BTreeSet<Vec<String>>) {
    for sub in cmd.get_subcommands() {
        let mut path = prefix.clone();
        path.push(sub.get_name().to_string());
        out.insert(path.clone());
        rust_paths(sub, path, out);
    }
}

#[test]
fn clap_tree_matches_python_surface() {
    let fixture: Value =
        serde_json::from_str(include_str!("fixtures/python-cli-surface.json")).unwrap();
    let mut root = Cli::command();
    root.build();

    let mut problems = Vec::new();
    let mut python_paths = BTreeSet::new();
    for entry in fixture.as_array().unwrap() {
        let path = strings_unsorted(&entry["path"]);
        python_paths.insert(path.clone());
        let label = if path.is_empty() {
            "(root)".to_string()
        } else {
            path.join(" ")
        };
        let Some(cmd) = find(&root, &path) else {
            problems.push(format!("missing command: bcli {label}"));
            continue;
        };
        let (py_opts, rs_opts) = (python_options(entry), rust_options(cmd));
        for o in py_opts.difference(&rs_opts) {
            problems.push(format!("{label}: python option not matched in clap: {o:?}"));
        }
        for o in rs_opts.difference(&py_opts) {
            problems.push(format!("{label}: clap option not in python: {o:?}"));
        }
        let (py_pos, rs_pos) = (python_positionals(entry), rust_positionals(cmd));
        if py_pos != rs_pos {
            problems.push(format!(
                "{label}: positionals differ\n  python {py_pos:?}\n  rust   {rs_pos:?}"
            ));
        }
    }

    let mut ours = BTreeSet::new();
    rust_paths(&root, Vec::new(), &mut ours);
    for extra in ours.difference(&python_paths) {
        problems.push(format!(
            "clap command not in python: bcli {}",
            extra.join(" ")
        ));
    }

    assert!(python_paths.len() > 50, "fixture looks truncated");
    assert!(
        problems.is_empty(),
        "{} surface mismatches:\n{}",
        problems.len(),
        problems.join("\n")
    );
}

fn strings_unsorted(v: &Value) -> Vec<String> {
    v.as_array()
        .unwrap()
        .iter()
        .map(|s| s.as_str().unwrap().to_string())
        .collect()
}
