//! User-facing error text with a remediation hint (`bcli_cli._error_handler`).

use bcli_core::{BcliError, ErrorKind};

pub fn format_for_cli(
    err: &BcliError,
    active_profile: Option<&str>,
    available: Option<Vec<String>>,
) -> String {
    let base = err.to_string();
    let mut extras = Vec::new();
    match err.kind {
        ErrorKind::Auth if !base.contains("bcli auth login") => extras.push(match active_profile {
            Some(p) => format!("Run 'bcli auth login --profile {p}' to re-authenticate."),
            None => "Run 'bcli auth login' to authenticate.".to_string(),
        }),
        ErrorKind::Config => {
            if !base.contains("bcli config init") {
                extras.push("Run 'bcli config init' to create a profile.".to_string());
            }
            if let (Some(active), Some(available)) = (active_profile, available) {
                if !available.iter().any(|p| p == active) {
                    let candidates: Vec<&str> = available.iter().map(String::as_str).collect();
                    let matches = difflib::get_close_matches(active, candidates, 3, 0.5);
                    if !matches.is_empty() {
                        extras.push(format!("Did you mean: {}?", matches.join(", ")));
                    }
                }
            }
        }
        ErrorKind::Registry if !base.contains("Did you mean") && !base.contains("bcli registry import") => {
            extras.push(
                "Run 'bcli registry import --from-metadata <metadata-url>' or 'bcli registry import \
                 --from-postman <file.json>' to register the endpoint."
                    .to_string(),
            )
        }
        _ => {}
    }
    if extras.is_empty() {
        base
    } else {
        format!("{base}\n  {}", extras.join("\n  "))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn auth_errors_get_a_login_hint() {
        let msg = format_for_cli(&BcliError::auth("expired"), Some("prod"), None);
        assert_eq!(
            msg,
            "expired\n  Run 'bcli auth login --profile prod' to re-authenticate."
        );
    }

    #[test]
    fn unknown_profile_gets_did_you_mean() {
        let err =
            BcliError::config("Profile 'prdo' not found. Run 'bcli config init --profile prdo'");
        let msg = format_for_cli(
            &err,
            Some("prdo"),
            Some(vec!["prod".into(), "sandbox".into()]),
        );
        assert!(msg.ends_with("\n  Did you mean: prod?"), "{msg}");
    }
}
