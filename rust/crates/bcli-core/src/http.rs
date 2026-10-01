//! HTTP transport (`bcli.client._transport`): bearer injection, retry on
//! 429/503/504 and network errors for idempotent methods, and BC error
//! parsing into the typed error taxonomy.

use std::time::Duration;

use reqwest::header::{HeaderMap, HeaderValue, ACCEPT, CONTENT_TYPE};
use reqwest::{Method, Response, StatusCode};
use serde_json::Value;

use crate::auth::Authenticator;
use crate::error::{BcliError, ErrorKind, Result};

pub const DEFAULT_MAX_RETRIES: u32 = 3;
const RETRYABLE: [u16; 3] = [429, 503, 504];
const CORRELATION_HEADER: &str = "x-ms-correlation-request-id";

pub struct Transport<'a> {
    client: reqwest::Client,
    auth: Authenticator<'a>,
    pub max_retries: u32,
    pub initial_backoff: Duration,
}

impl<'a> Transport<'a> {
    pub fn new(auth: Authenticator<'a>, timeout: Duration) -> Result<Self> {
        let mut headers = HeaderMap::new();
        headers.insert(CONTENT_TYPE, HeaderValue::from_static("application/json"));
        headers.insert(ACCEPT, HeaderValue::from_static("application/json"));
        headers.insert("OData-Version", HeaderValue::from_static("4.0"));
        let client = reqwest::Client::builder()
            .default_headers(headers)
            .timeout(timeout)
            .user_agent(concat!("bcli/", env!("CARGO_PKG_VERSION")))
            .build()
            .map_err(|e| {
                BcliError::new(ErrorKind::Generic, format!("Cannot build HTTP client: {e}"))
            })?;
        Ok(Self {
            client,
            auth,
            max_retries: DEFAULT_MAX_RETRIES,
            initial_backoff: Duration::from_secs(1),
        })
    }

    pub fn take_warnings(&mut self) -> Vec<String> {
        self.auth.take_warnings()
    }

    pub async fn get_json(&mut self, url: &str) -> Result<Value> {
        self.request(Method::GET, url).await
    }

    async fn request(&mut self, method: Method, url: &str) -> Result<Value> {
        let retry_safe = method == Method::GET || method == Method::HEAD;
        let mut backoff = self.initial_backoff;
        let mut attempt = 0;
        loop {
            let token = self.auth.access_token(&self.client).await?;
            let sent = self
                .client
                .request(method.clone(), url)
                .bearer_auth(token)
                .send()
                .await;

            let response = match sent {
                Ok(response) => response,
                Err(e) if is_network_error(&e) && attempt < self.max_retries && retry_safe => {
                    tokio::time::sleep(backoff).await;
                    backoff *= 2;
                    attempt += 1;
                    continue;
                }
                Err(e) => {
                    return Err(BcliError::server(format!(
                        "Network error after {} attempts: {e}",
                        attempt + 1
                    )))
                }
            };

            let status = response.status();
            if status.is_success() {
                let bytes = response.bytes().await.map_err(|e| {
                    BcliError::server(format!("Failed reading response from {url}: {e}"))
                })?;
                if status == StatusCode::NO_CONTENT || bytes.is_empty() {
                    return Ok(Value::Object(Default::default()));
                }
                return serde_json::from_slice(&bytes)
                    .map_err(|e| BcliError::server(format!("Invalid JSON from {url}: {e}")));
            }

            if RETRYABLE.contains(&status.as_u16()) && attempt < self.max_retries && retry_safe {
                let wait = retry_after(&response).unwrap_or(backoff);
                tokio::time::sleep(wait).await;
                backoff *= 2;
                attempt += 1;
                continue;
            }

            return Err(error_from_response(&method, url, response).await);
        }
    }
}

fn is_network_error(e: &reqwest::Error) -> bool {
    e.is_connect() || e.is_timeout() || e.is_request()
}

fn retry_after(response: &Response) -> Option<Duration> {
    let secs: f64 = response
        .headers()
        .get("Retry-After")?
        .to_str()
        .ok()?
        .trim()
        .parse()
        .ok()?;
    (secs.is_finite() && secs >= 0.0).then(|| Duration::from_secs_f64(secs))
}

async fn error_from_response(method: &Method, url: &str, response: Response) -> BcliError {
    let status = response.status();
    let correlation_id = response
        .headers()
        .get(CORRELATION_HEADER)
        .and_then(|v| v.to_str().ok())
        .map(str::to_owned);
    let retry = retry_after(&response);
    let bc_message = response.json::<Value>().await.ok().and_then(|body| {
        body.get("error")?
            .get("message")?
            .as_str()
            .map(str::to_owned)
    });

    let reason = status.canonical_reason().unwrap_or("");
    let mut message = format!("HTTP {} {reason}: {method} {url}", status.as_u16());
    if let Some(hint) = hint_for_bc_error(status.as_u16(), bc_message.as_deref(), url) {
        message.push_str(&format!("\n  Hint: {hint}"));
    }
    let mut err = BcliError::new(BcliError::kind_for_status(status.as_u16()), message)
        .with_status(status.as_u16())
        .with_bc_message(bc_message)
        .with_correlation_id(correlation_id);
    err.retry_after = retry.map(|d| d.as_secs_f64());
    err
}

/// Matches `Could not find a property named '<field>' on type 'Microsoft.NAV.<word>'`.
fn is_property_not_found(msg: &str) -> bool {
    const PREFIX: &str = "Could not find a property named '";
    const TYPE_PREFIX: &str = "' on type 'Microsoft.NAV.";
    msg.match_indices(PREFIX).any(|(i, _)| {
        let rest = &msg[i + PREFIX.len()..];
        let Some(field_end) = rest.find('\'') else {
            return false;
        };
        let Some(type_rest) = rest[field_end..].strip_prefix(TYPE_PREFIX) else {
            return false;
        };
        let word_len = type_rest
            .find(|c: char| !(c.is_alphanumeric() || c == '_'))
            .unwrap_or(type_rest.len());
        field_end > 0 && word_len > 0 && type_rest[word_len..].starts_with('\'')
    })
}

/// Next-command hint for BC's "property not found" 400 (`_hint_for_bc_error`).
fn hint_for_bc_error(status: u16, bc_message: Option<&str>, url: &str) -> Option<String> {
    let msg = bc_message?;
    if status != 400 || !is_property_not_found(msg) {
        return None;
    }
    match entity_from_url(url) {
        Some(entity) => Some(format!(
            "Run 'bcli endpoint fields {entity}' to discover the actual field names on this \
             endpoint. Don't guess them — BC custom APIs don't always follow obvious naming."
        )),
        None => Some(
            "Run 'bcli endpoint fields <endpoint>' to discover the actual field names on this endpoint."
                .into(),
        ),
    }
}

/// The entity-set segment after `/companies(<id>)/`.
fn entity_from_url(url: &str) -> Option<&str> {
    let start = url.find("/companies(")?;
    let rest = &url[start + "/companies(".len()..];
    let after = &rest[rest.find(")/")? + 2..];
    let end = after
        .find(|c: char| !(c.is_ascii_alphanumeric() || c == '_'))
        .unwrap_or(after.len());
    (end > 0).then(|| &after[..end])
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn property_hint_names_the_entity() {
        let url = "https://x/v2.0/P/api/v2.0/companies(abc)/vendors?$filter=x";
        let msg = "Could not find a property named 'serialNumber' on type 'Microsoft.NAV.vendor'.";
        let hint = hint_for_bc_error(400, Some(msg), url).unwrap();
        assert!(hint.starts_with("Run 'bcli endpoint fields vendors'"));
        assert!(hint_for_bc_error(404, Some(msg), url).is_none());
        assert!(hint_for_bc_error(400, Some("other"), url).is_none());
    }

    #[test]
    fn entity_from_url_handles_keys() {
        assert_eq!(
            entity_from_url("https://x/companies(1)/items(2)"),
            Some("items")
        );
        assert_eq!(entity_from_url("https://x/companies"), None);
    }
}
