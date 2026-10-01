//! Per-invocation state (`bcli_cli._state.CLIState`): global flags plus the
//! lazily loaded config, so commands that never touch config (`--version`,
//! `--help`) never read it.

use std::time::Duration;

use bcli_core::client::BcClient;
use bcli_core::config::{self, Config, Profile};
use bcli_core::paths::Paths;
use bcli_core::registry::{Registry, RegistryOptions};
use bcli_core::Result;

use crate::cli::GlobalArgs;
use crate::output::detect_default_format;
use crate::Env;

pub struct Context<'a> {
    pub global: &'a GlobalArgs,
    pub env: &'a Env,
    pub paths: Paths,
    /// `--format`, else auto-detected.
    pub format: String,
    /// Suppresses the context banner; implied by machine-readable formats.
    pub quiet: bool,
    config: Option<Config>,
    warnings: Vec<String>,
}

impl<'a> Context<'a> {
    pub fn new(global: &'a GlobalArgs, env: &'a Env, stdout_is_tty: bool) -> Self {
        let format = global
            .format
            .clone()
            .unwrap_or_else(|| detect_default_format(&*env.vars, stdout_is_tty));
        let quiet = global.quiet || matches!(format.as_str(), "json" | "csv" | "ndjson" | "raw");
        Self {
            global,
            env,
            paths: Paths::from_home(&env.home),
            format,
            quiet,
            config: None,
            warnings: Vec::new(),
        }
    }

    pub fn config(&mut self) -> Result<&Config> {
        if self.config.is_none() {
            let loaded = config::load(&self.paths, self.env.cwd.as_deref(), &*self.env.vars)?;
            self.warnings.extend(loaded.warnings);
            self.config = Some(loaded.config);
        }
        Ok(self.config.get_or_insert_with(Config::default))
    }

    pub fn take_warnings(&mut self) -> Vec<String> {
        std::mem::take(&mut self.warnings)
    }

    /// Profile names, if config has been loaded (for "Did you mean" hints).
    pub fn profile_names(&self) -> Option<Vec<String>> {
        self.config
            .as_ref()
            .map(|c| c.profiles.keys().cloned().collect())
    }

    pub fn active_profile_name(&mut self) -> Result<String> {
        match &self.global.profile {
            Some(name) => Ok(name.clone()),
            None => Ok(self.config()?.defaults.profile.clone()),
        }
    }

    /// The profile as written in config, without `--env` / `--company`.
    pub fn raw_profile(&mut self) -> Result<Profile> {
        let name = self.global.profile.clone();
        Ok(self.config()?.get_profile(name.as_deref())?.clone())
    }

    /// The profile with `--env` / `--company` applied.
    pub fn profile(&mut self) -> Result<Profile> {
        let (env, company) = (self.global.env.clone(), self.global.company.clone());
        self.raw_profile()?
            .with_overrides(env.as_deref(), company.as_deref())
    }

    pub fn registry(&mut self) -> Result<Registry> {
        let name = self.active_profile_name()?;
        let profile = self.raw_profile()?;
        Registry::load(
            Some(&self.paths.custom_registry_file(&name)),
            &RegistryOptions {
                disable_standard: profile.disable_standard_api,
                allowed_categories: profile.allowed_categories,
                allowed_endpoints: profile.allowed_endpoints,
            },
        )
    }

    /// Client bound to the overridden profile (`CLIState.make_async_client`).
    pub fn client(&mut self) -> Result<BcClient<'a>> {
        let profile = self.profile()?;
        let timeout = Duration::from_secs(self.config()?.defaults.timeout);
        let env = self.env;
        BcClient::new(
            profile,
            &self.paths,
            &env.urls,
            &*env.secrets,
            &*env.vars,
            timeout,
        )
    }
}
