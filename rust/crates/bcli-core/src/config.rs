//! Configuration model and layered loader (`bcli.config`).
//!
//! Resolution order, identical to the Python loader:
//! 1. global `~/.config/bcli/config.toml`
//! 2. project `.bcli.toml` found by walking up from the cwd, with
//!    `[telemetry]` reduced to `enabled` (a checked-out repo must not be able
//!    to select a telemetry backend)
//! 3. `BCLI_PROFILE` / `BCLI_FORMAT` / `BCLI_TIMEOUT`
//!
//! Unknown keys and sections (`[telemetry]`, `[audit]`, `[ask]`, …) are kept
//! in `extra` so a future `save` can round-trip them.

use std::path::{Path, PathBuf};

use indexmap::IndexMap;
use serde::{Deserialize, Serialize};
use toml::{Table, Value};

use crate::error::{BcliError, Result};
use crate::paths::{Paths, PROJECT_CONFIG_FILE};

pub const DEFAULT_FORMAT: &str = "table";
pub const DEFAULT_PAGE_SIZE: u32 = 100;
pub const DEFAULT_TIMEOUT: u64 = 60;

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CompanyAlias {
    pub id: String,
    #[serde(default)]
    pub name: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Profile {
    pub tenant_id: String,
    pub environment: String,
    #[serde(default)]
    pub company_id: Option<String>,
    #[serde(default)]
    pub company_name: Option<String>,
    #[serde(default)]
    pub companies: IndexMap<String, CompanyAlias>,

    #[serde(default = "default_auth_method")]
    pub auth_method: String,
    #[serde(default)]
    pub client_id: Option<String>,
    #[serde(default)]
    pub client_secret_env: Option<String>,

    #[serde(default)]
    pub api_publisher: Option<String>,
    #[serde(default)]
    pub api_group: Option<String>,
    #[serde(default)]
    pub api_version: Option<String>,

    #[serde(default)]
    pub disable_standard_api: bool,
    #[serde(default)]
    pub allowed_categories: Vec<String>,
    #[serde(default)]
    pub allowed_endpoints: Vec<String>,
    #[serde(default)]
    pub disable_writes: bool,

    #[serde(flatten)]
    pub extra: Table,
}

fn default_auth_method() -> String {
    "client_credentials".into()
}

/// Result of resolving `--company`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CompanySelection {
    One {
        id: String,
        name: Option<String>,
    },
    /// `--company all`: the caller iterates over every known company.
    All,
}

impl Profile {
    /// `BCProfile.resolve_company`: alias (exact, then case-insensitive), a
    /// GUID-looking string, `all`, or the profile default when `None`.
    pub fn resolve_company(&self, alias_or_id: Option<&str>) -> Result<CompanySelection> {
        let Some(value) = alias_or_id else {
            return match &self.company_id {
                Some(id) if !id.is_empty() => Ok(CompanySelection::One {
                    id: id.clone(),
                    name: self.company_name.clone(),
                }),
                _ => Err(BcliError::config(
                    "No company_id configured. Run 'bcli company list' and 'bcli company use <id>'.",
                )),
            };
        };

        if value.eq_ignore_ascii_case("all") {
            return Ok(CompanySelection::All);
        }
        if let Some(alias) = self.companies.get(value) {
            return Ok(one(alias, value));
        }
        if value.chars().count() > 8 && value.contains('-') {
            return Ok(CompanySelection::One {
                id: value.to_string(),
                name: None,
            });
        }
        let lowered = value.to_lowercase();
        if let Some((key, alias)) = self
            .companies
            .iter()
            .find(|(key, _)| key.to_lowercase() == lowered)
        {
            return Ok(one(alias, key));
        }

        let available = if self.companies.is_empty() {
            "(none)".to_string()
        } else {
            self.companies
                .keys()
                .cloned()
                .collect::<Vec<_>>()
                .join(", ")
        };
        Err(BcliError::config(format!(
            "Company alias '{value}' not found. Available: {available}. \
             Use 'bcli company alias <name> <id>' to create one."
        )))
    }

    /// Apply `--env` / `--company` the way `CLIState.profile` does.
    pub fn with_overrides(&self, env: Option<&str>, company: Option<&str>) -> Result<Profile> {
        let mut profile = self.clone();
        if let Some(env) = env.filter(|e| !e.is_empty()) {
            profile.environment = env.to_string();
        }
        if let Some(company) = company.filter(|c| !c.is_empty()) {
            if let CompanySelection::One { id, name } = self.resolve_company(Some(company))? {
                profile.company_id = Some(id);
                profile.company_name = Some(name.unwrap_or_else(|| company.to_string()));
            }
        }
        Ok(profile)
    }
}

fn one(alias: &CompanyAlias, fallback_name: &str) -> CompanySelection {
    let name = if alias.name.is_empty() {
        fallback_name.to_string()
    } else {
        alias.name.clone()
    };
    CompanySelection::One {
        id: alias.id.clone(),
        name: Some(name),
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Defaults {
    #[serde(default = "default_profile_name")]
    pub profile: String,
    #[serde(default = "default_format")]
    pub format: String,
    #[serde(default = "default_page_size")]
    pub page_size: u32,
    #[serde(default = "default_timeout")]
    pub timeout: u64,
    #[serde(flatten)]
    pub extra: Table,
}

impl Default for Defaults {
    fn default() -> Self {
        Self {
            profile: default_profile_name(),
            format: default_format(),
            page_size: DEFAULT_PAGE_SIZE,
            timeout: DEFAULT_TIMEOUT,
            extra: Table::new(),
        }
    }
}

fn default_profile_name() -> String {
    "default".into()
}
fn default_format() -> String {
    DEFAULT_FORMAT.into()
}
fn default_page_size() -> u32 {
    DEFAULT_PAGE_SIZE
}
fn default_timeout() -> u64 {
    DEFAULT_TIMEOUT
}

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct Config {
    #[serde(default)]
    pub defaults: Defaults,
    #[serde(default)]
    pub profiles: IndexMap<String, Profile>,
    #[serde(flatten)]
    pub extra: Table,
}

impl Config {
    /// `BCConfig.get_profile`, with the same remediation text.
    pub fn get_profile(&self, name: Option<&str>) -> Result<&Profile> {
        let name = name.unwrap_or(&self.defaults.profile);
        if let Some(profile) = self.profiles.get(name) {
            return Ok(profile);
        }
        if self.profiles.is_empty() {
            return Err(BcliError::config(
                "No profiles configured. Run 'bcli config init' to create your first profile.",
            ));
        }
        let available = self.profiles.keys().cloned().collect::<Vec<_>>().join(", ");
        Err(BcliError::config(format!(
            "Profile '{name}' not found. Available: {available}. \
             Run 'bcli config init --profile {name}' to create it, \
             or 'bcli config use <name>' to switch."
        )))
    }
}

/// A non-fatal message the loader wants surfaced on stderr.
pub type Warning = String;

pub struct Loaded {
    pub config: Config,
    pub warnings: Vec<Warning>,
}

pub fn load(
    paths: &Paths,
    cwd: Option<&Path>,
    env: &dyn Fn(&str) -> Option<String>,
) -> Result<Loaded> {
    let mut warnings = Vec::new();
    let global = load_toml(&paths.config_file())?;
    let project = match cwd.and_then(find_project_config) {
        Some(path) => sanitise_project_config(load_toml(&path)?, &path, &mut warnings),
        None => Table::new(),
    };

    let mut merged = deep_merge(global, project);
    apply_env_overrides(&mut merged, env)?;

    let config: Config = Value::Table(merged)
        .try_into()
        .map_err(|e| BcliError::config(format!("Invalid bcli configuration: {e}")))?;
    Ok(Loaded { config, warnings })
}

fn find_project_config(cwd: &Path) -> Option<PathBuf> {
    cwd.ancestors()
        .map(|dir| dir.join(PROJECT_CONFIG_FILE))
        .find(|candidate| candidate.is_file())
}

fn load_toml(path: &Path) -> Result<Table> {
    if !path.is_file() {
        return Ok(Table::new());
    }
    let text = std::fs::read_to_string(path)
        .map_err(|e| BcliError::config(format!("Cannot read {}: {e}", path.display())))?;
    text.parse::<Table>()
        .map_err(|e| BcliError::config(format!("Invalid TOML in {}: {e}", path.display())))
}

fn deep_merge(mut base: Table, overlay: Table) -> Table {
    for (key, value) in overlay {
        match (base.get_mut(&key), value) {
            (Some(Value::Table(existing)), Value::Table(incoming)) => {
                let merged = deep_merge(std::mem::take(existing), incoming);
                *existing = merged;
            }
            (_, value) => {
                base.insert(key, value);
            }
        }
    }
    base
}

fn sanitise_project_config(mut data: Table, source: &Path, warnings: &mut Vec<Warning>) -> Table {
    let Some(Value::Table(telemetry)) = data.get("telemetry") else {
        return data;
    };
    let mut rejected: Vec<&String> = telemetry
        .keys()
        .filter(|k| k.as_str() != "enabled")
        .collect();
    rejected.sort();
    if !rejected.is_empty() {
        let list = rejected
            .iter()
            .map(|k| format!("'{k}'"))
            .collect::<Vec<_>>()
            .join(", ");
        warnings.push(format!(
            "Ignoring [telemetry] keys [{list}] from project config {}: only 'enabled' is \
             honoured at the project layer (custom backends and connection strings must live \
             in the global config to prevent arbitrary code execution from a checked-out repo).",
            source.display()
        ));
    }
    match telemetry.get("enabled").cloned() {
        Some(enabled) => {
            let mut cleaned = Table::new();
            cleaned.insert("enabled".into(), enabled);
            data.insert("telemetry".into(), Value::Table(cleaned));
        }
        None => {
            data.remove("telemetry");
        }
    }
    data
}

fn apply_env_overrides(data: &mut Table, env: &dyn Fn(&str) -> Option<String>) -> Result<()> {
    for (var, key) in [
        ("BCLI_PROFILE", "profile"),
        ("BCLI_FORMAT", "format"),
        ("BCLI_TIMEOUT", "timeout"),
    ] {
        let Some(raw) = env(var) else { continue };
        let value = if key == "timeout" {
            let secs = raw
                .trim()
                .parse::<i64>()
                .map_err(|_| BcliError::config(format!("{var} must be an integer, got '{raw}'")))?;
            Value::Integer(secs)
        } else {
            Value::String(raw)
        };
        let section = data
            .entry("defaults")
            .or_insert_with(|| Value::Table(Table::new()));
        if let Value::Table(section) = section {
            section.insert(key.into(), value);
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    const GLOBAL: &str = r#"
[defaults]
profile = "prod"

[profiles.prod]
tenant_id = "t"
environment = "Production"
company_id = "11111111-1111-1111-1111-111111111111"
auth_method = "device_code"
client_id = "c"
disable_writes = true
future_key = "kept"

[profiles.prod.companies.LLC]
id = "22222222-2222-2222-2222-222222222222"
name = "Contoso LLC"

[profiles.sandbox]
tenant_id = "t"
environment = "Sandbox"

[telemetry]
enabled = true
backend = "console"
"#;

    fn setup(global: &str) -> (tempfile::TempDir, Paths) {
        let home = tempfile::tempdir().unwrap();
        let paths = Paths::from_home(home.path());
        std::fs::create_dir_all(&paths.config_dir).unwrap();
        std::fs::write(paths.config_file(), global).unwrap();
        (home, paths)
    }

    fn no_env(_: &str) -> Option<String> {
        None
    }

    #[test]
    fn loads_profiles_in_file_order_and_keeps_unknown_keys() {
        let (_home, paths) = setup(GLOBAL);
        let cfg = load(&paths, None, &no_env).unwrap().config;
        assert_eq!(cfg.profiles.keys().collect::<Vec<_>>(), ["prod", "sandbox"]);
        let prod = cfg.get_profile(None).unwrap();
        assert!(prod.disable_writes);
        assert_eq!(
            prod.extra.get("future_key").and_then(Value::as_str),
            Some("kept")
        );
        assert!(cfg.extra.contains_key("telemetry"));
        assert_eq!(cfg.profiles["sandbox"].auth_method, "client_credentials");
    }

    #[test]
    fn missing_profile_error_lists_available() {
        let (_home, paths) = setup(GLOBAL);
        let cfg = load(&paths, None, &no_env).unwrap().config;
        let err = cfg.get_profile(Some("nope")).unwrap_err();
        assert!(err
            .to_string()
            .starts_with("Profile 'nope' not found. Available: prod, sandbox."));
    }

    #[test]
    fn no_config_means_no_profiles() {
        let home = tempfile::tempdir().unwrap();
        let cfg = load(&Paths::from_home(home.path()), None, &no_env)
            .unwrap()
            .config;
        let err = cfg.get_profile(None).unwrap_err();
        assert_eq!(
            err.to_string(),
            "No profiles configured. Run 'bcli config init' to create your first profile."
        );
    }

    #[test]
    fn env_overrides_win() {
        let (_home, paths) = setup(GLOBAL);
        let env = |k: &str| match k {
            "BCLI_PROFILE" => Some("sandbox".to_string()),
            "BCLI_TIMEOUT" => Some("5".to_string()),
            _ => None,
        };
        let cfg = load(&paths, None, &env).unwrap().config;
        assert_eq!(cfg.defaults.profile, "sandbox");
        assert_eq!(cfg.defaults.timeout, 5);
    }

    #[test]
    fn project_config_cannot_select_a_telemetry_backend() {
        let (home, paths) = setup(GLOBAL);
        let project = home.path().join("repo").join("nested");
        std::fs::create_dir_all(&project).unwrap();
        std::fs::write(
            home.path().join("repo").join(PROJECT_CONFIG_FILE),
            "[telemetry]\nenabled = false\nbackend = \"evil:Sink\"\n",
        )
        .unwrap();
        let loaded = load(&paths, Some(&project), &no_env).unwrap();
        let telemetry = loaded.config.extra["telemetry"].as_table().unwrap();
        assert_eq!(telemetry["enabled"].as_bool(), Some(false));
        assert_eq!(telemetry["backend"].as_str(), Some("console"));
        assert_eq!(loaded.warnings.len(), 1);
        assert!(loaded.warnings[0].contains("'backend'"));
    }

    #[test]
    fn resolve_company_matches_python_rules() {
        let (_home, paths) = setup(GLOBAL);
        let cfg = load(&paths, None, &no_env).unwrap().config;
        let prod = cfg.get_profile(None).unwrap();
        let llc = CompanySelection::One {
            id: "22222222-2222-2222-2222-222222222222".into(),
            name: Some("Contoso LLC".into()),
        };
        assert_eq!(prod.resolve_company(Some("LLC")).unwrap(), llc);
        assert_eq!(prod.resolve_company(Some("llc")).unwrap(), llc);
        assert_eq!(
            prod.resolve_company(Some("ALL")).unwrap(),
            CompanySelection::All
        );
        assert_eq!(
            prod.resolve_company(Some("33333333-3333")).unwrap(),
            CompanySelection::One {
                id: "33333333-3333".into(),
                name: None
            }
        );
        let err = prod.resolve_company(Some("XYZ")).unwrap_err();
        assert!(err
            .to_string()
            .starts_with("Company alias 'XYZ' not found. Available: LLC."));
    }

    #[test]
    fn overrides_replace_env_and_company() {
        let (_home, paths) = setup(GLOBAL);
        let cfg = load(&paths, None, &no_env).unwrap().config;
        let p = cfg
            .get_profile(None)
            .unwrap()
            .with_overrides(Some("Sandbox"), Some("LLC"))
            .unwrap();
        assert_eq!(p.environment, "Sandbox");
        assert_eq!(p.company_name.as_deref(), Some("Contoso LLC"));
        let unchanged = cfg
            .get_profile(None)
            .unwrap()
            .with_overrides(None, Some("all"))
            .unwrap();
        assert_eq!(
            unchanged.company_id.as_deref(),
            Some("11111111-1111-1111-1111-111111111111")
        );
    }
}
