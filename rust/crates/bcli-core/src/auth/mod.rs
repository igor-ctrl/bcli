//! Token acquisition (`bcli.auth`).
//!
//! Ported: the shared `tokens.json` cache (every method checks it first) and
//! the `client_credentials` flow against the Entra v2.0 token endpoint.
//! Not ported yet: interactive `browser` / `device_code` sign-in and silent
//! renewal from MSAL's `msal_cache.json`; those profiles work for as long as
//! the cached access token (from a Python `bcli auth login`) is valid.

pub mod secure_io;
pub mod token_cache;

use chrono::Utc;
use serde::Deserialize;

use crate::config::Profile;
use crate::error::{BcliError, Result};
use crate::paths::Paths;
use crate::url::{ServiceUrls, BC_SCOPE};
use token_cache::TokenCache;

pub const KEYRING_SERVICE: &str = "bcli";

/// Read-only view of the OS keychain, injectable for tests.
pub trait SecretStore {
    fn get(&self, service: &str, user: &str) -> Option<String>;
}

pub struct NoSecretStore;

impl SecretStore for NoSecretStore {
    fn get(&self, _service: &str, _user: &str) -> Option<String> {
        None
    }
}

#[cfg(feature = "keyring")]
pub struct OsKeyring;

#[cfg(feature = "keyring")]
impl SecretStore for OsKeyring {
    fn get(&self, service: &str, user: &str) -> Option<String> {
        keyring::Entry::new(service, user).ok()?.get_password().ok()
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AuthMethod {
    ClientCredentials,
    Browser,
    DeviceCode,
}

impl AuthMethod {
    pub fn parse(value: &str) -> Result<Self> {
        match value {
            "client_credentials" | "client-credentials" => Ok(Self::ClientCredentials),
            "browser" => Ok(Self::Browser),
            "device_code" => Ok(Self::DeviceCode),
            other => Err(BcliError::config(format!(
                "Unsupported auth_method '{other}'. Use 'browser', 'device_code', or 'client_credentials'."
            ))),
        }
    }

    fn as_str(self) -> &'static str {
        match self {
            Self::ClientCredentials => "client_credentials",
            Self::Browser => "browser",
            Self::DeviceCode => "device_code",
        }
    }
}

pub struct Authenticator<'a> {
    method: AuthMethod,
    tenant_id: String,
    client_id: String,
    client_secret_env: Option<String>,
    cache: TokenCache,
    urls: &'a ServiceUrls,
    secrets: &'a dyn SecretStore,
    env: &'a dyn Fn(&str) -> Option<String>,
}

#[derive(Deserialize)]
struct TokenResponse {
    access_token: Option<String>,
    expires_in: Option<i64>,
    error: Option<String>,
    error_description: Option<String>,
}

impl<'a> Authenticator<'a> {
    pub fn for_profile(
        profile: &Profile,
        paths: &Paths,
        urls: &'a ServiceUrls,
        secrets: &'a dyn SecretStore,
        env: &'a dyn Fn(&str) -> Option<String>,
    ) -> Result<Self> {
        Ok(Self {
            method: AuthMethod::parse(&profile.auth_method)?,
            tenant_id: profile.tenant_id.clone(),
            client_id: profile.client_id.clone().unwrap_or_default(),
            client_secret_env: profile.client_secret_env.clone(),
            cache: TokenCache::new(paths.token_cache_file()),
            urls,
            secrets,
            env,
        })
    }

    pub fn take_warnings(&mut self) -> Vec<String> {
        std::mem::take(&mut self.cache.warnings)
    }

    pub async fn access_token(&mut self, http: &reqwest::Client) -> Result<String> {
        if let Some(token) = self.cache.get(&self.tenant_id, &self.client_id, Utc::now()) {
            return Ok(token);
        }
        match self.method {
            AuthMethod::ClientCredentials => self.client_credentials(http).await,
            AuthMethod::Browser | AuthMethod::DeviceCode => Err(BcliError::auth(format!(
                "No valid cached token for auth_method '{}', and interactive sign-in is not yet \
                 ported to the Rust build. Sign in with the Python bcli ('bcli auth login'); \
                 the Rust build reuses the cached token.",
                self.method.as_str()
            ))),
        }
    }

    /// Keychain, then `client_secret_env`, then `BCLI_CLIENT_SECRET` / `BCLI_SECRET`.
    fn resolve_secret(&self) -> Result<String> {
        let keyring_user = format!("{}:{}", self.tenant_id, self.client_id);
        if let Some(secret) = self
            .secrets
            .get(KEYRING_SERVICE, &keyring_user)
            .filter(|s| !s.is_empty())
        {
            return Ok(secret);
        }
        let from_env = |name: &str| (self.env)(name).filter(|s| !s.is_empty());
        if let Some(secret) = self.client_secret_env.as_deref().and_then(from_env) {
            return Ok(secret);
        }
        if let Some(secret) = from_env("BCLI_CLIENT_SECRET").or_else(|| from_env("BCLI_SECRET")) {
            return Ok(secret);
        }
        let mut hints = vec!["bcli auth store-secret  (saves to OS keychain)".to_string()];
        if let Some(var) = &self.client_secret_env {
            hints.push(format!("export {var}=<secret>"));
        }
        Err(BcliError::config(format!(
            "No client secret found. Options:\n  {}",
            hints.join("\n  ")
        )))
    }

    async fn client_credentials(&mut self, http: &reqwest::Client) -> Result<String> {
        let secret = self.resolve_secret()?;
        let url = format!(
            "{}/{}/oauth2/v2.0/token",
            self.urls.authority_base, self.tenant_id
        );
        let response = http
            .post(&url)
            .form(&[
                ("client_id", self.client_id.as_str()),
                ("client_secret", secret.as_str()),
                ("scope", BC_SCOPE),
                ("grant_type", "client_credentials"),
            ])
            .send()
            .await
            .map_err(|e| {
                BcliError::auth(format!("Failed to acquire token: {e}")).with_status(401)
            })?;
        let body: TokenResponse = response.json().await.map_err(|e| {
            BcliError::auth(format!("Failed to acquire token: {e}")).with_status(401)
        })?;

        let Some(token) = body.access_token else {
            let detail = body
                .error_description
                .or(body.error)
                .unwrap_or_else(|| "Unknown error".into());
            return Err(
                BcliError::auth(format!("Failed to acquire token: {detail}")).with_status(401),
            );
        };
        let expires_in = body.expires_in.unwrap_or(3600);
        self.cache.put(
            &self.tenant_id,
            &self.client_id,
            &token,
            expires_in,
            Utc::now(),
        )?;
        Ok(token)
    }
}
