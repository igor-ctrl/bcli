//! High-level Business Central client (`bcli.client.AsyncBCClient`).

use std::time::Duration;

use serde_json::{Map, Value};

use crate::auth::{Authenticator, SecretStore};
use crate::config::Profile;
use crate::error::Result;
use crate::http::Transport;
use crate::paths::Paths;
use crate::url::{build_companies_url, ServiceUrls};

pub type Record = Map<String, Value>;

pub struct BcClient<'a> {
    pub transport: Transport<'a>,
    profile: Profile,
    urls: &'a ServiceUrls,
}

impl<'a> BcClient<'a> {
    pub fn new(
        profile: Profile,
        paths: &Paths,
        urls: &'a ServiceUrls,
        secrets: &'a dyn SecretStore,
        env: &'a dyn Fn(&str) -> Option<String>,
        timeout: Duration,
    ) -> Result<Self> {
        let auth = Authenticator::for_profile(&profile, paths, urls, secrets, env)?;
        Ok(Self {
            transport: Transport::new(auth, timeout)?,
            profile,
            urls,
        })
    }

    pub fn profile(&self) -> &Profile {
        &self.profile
    }

    /// All companies in the profile's environment (single request, no paging).
    pub async fn list_companies(&mut self) -> Result<Vec<Record>> {
        let url = build_companies_url(self.urls, &self.profile.environment);
        let body = self.transport.get_json(&url).await?;
        Ok(value_array(body))
    }
}

/// `data.get("value", [])`, keeping only object rows.
fn value_array(body: Value) -> Vec<Record> {
    match body {
        Value::Object(mut obj) => match obj.remove("value") {
            Some(Value::Array(items)) => items
                .into_iter()
                .filter_map(|v| match v {
                    Value::Object(o) => Some(o),
                    _ => None,
                })
                .collect(),
            _ => Vec::new(),
        },
        _ => Vec::new(),
    }
}
