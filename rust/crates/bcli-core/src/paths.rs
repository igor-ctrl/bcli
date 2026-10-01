//! On-disk locations (`bcli.config._defaults`).
//!
//! The Python CLI uses `~/.config/bcli` on every platform, Windows included
//! (`Path.home() / ".config" / "bcli"`), and the Beautech installer stages
//! files there. The Rust build must not switch to platform-native dirs
//! (`%APPDATA%`, `~/Library/Application Support`) or existing installs break.

use std::path::{Path, PathBuf};

pub const PROJECT_CONFIG_FILE: &str = ".bcli.toml";

#[derive(Debug, Clone)]
pub struct Paths {
    pub config_dir: PathBuf,
}

impl Paths {
    pub fn from_home(home: &Path) -> Self {
        Self {
            config_dir: home.join(".config").join("bcli"),
        }
    }

    pub fn config_file(&self) -> PathBuf {
        self.config_dir.join("config.toml")
    }

    /// bcli's own access-token cache (short-lived bearer tokens).
    pub fn token_cache_file(&self) -> PathBuf {
        self.config_dir.join("tokens.json")
    }

    /// MSAL's serialized cache (refresh tokens). Not read by the Rust build yet.
    pub fn msal_cache_file(&self) -> PathBuf {
        self.config_dir.join("msal_cache.json")
    }

    pub fn registries_dir(&self) -> PathBuf {
        self.config_dir.join("registries")
    }

    pub fn custom_registry_file(&self, profile: &str) -> PathBuf {
        self.registries_dir().join(format!("{profile}.json"))
    }
}
