//! URL construction and origin checks (`bcli._url`).

use crate::error::{BcliError, Result};

pub const BC_BASE_URL: &str = "https://api.businesscentral.dynamics.com/v2.0";
pub const BC_ADMIN_BASE_URL: &str = "https://api.businesscentral.dynamics.com/admin/v2.1";
pub const BC_STANDARD_API_PATH: &str = "api/v2.0";
pub const BC_SCOPE: &str = "https://api.businesscentral.dynamics.com/.default";
pub const ENTRA_AUTHORITY_BASE: &str = "https://login.microsoftonline.com";

const ALLOWED_HOST_SUFFIXES: [&str; 2] = ["businesscentral.dynamics.com", "bc.dynamics.com"];

/// Service base URLs. Production values by default; tests point them at a
/// local mock server. There is deliberately no env var or flag to change
/// them in a shipped binary: a bearer token follows these URLs.
#[derive(Debug, Clone)]
pub struct ServiceUrls {
    pub bc_base: String,
    pub bc_admin_base: String,
    pub authority_base: String,
}

impl Default for ServiceUrls {
    fn default() -> Self {
        Self {
            bc_base: BC_BASE_URL.into(),
            bc_admin_base: BC_ADMIN_BASE_URL.into(),
            authority_base: ENTRA_AUTHORITY_BASE.into(),
        }
    }
}

/// Custom API route: `api/{publisher}/{group}/{version}`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ApiRoute {
    pub publisher: String,
    pub group: String,
    pub version: String,
}

fn validate_route_segment(name: &str, value: &str) -> Result<()> {
    if value.trim().is_empty() {
        return Err(BcliError::validation(format!(
            "Invalid custom-API route segment '{name}': must not be empty."
        )));
    }
    if value.contains('/') || value.contains('\\') {
        return Err(BcliError::validation(format!(
            "Invalid custom-API route segment '{name}' = '{value}': must not contain '/' or '\\'."
        )));
    }
    if value == "." || value == ".." {
        return Err(BcliError::validation(format!(
            "Invalid custom-API route segment '{name}' = '{value}': \
             must not contain '.' or '..' path-traversal sequences."
        )));
    }
    Ok(())
}

/// A key or entity-set name must stay a single path component: `/`, `\`,
/// `?` and `#` would let it address a different resource than the one the
/// registry and `disable_standard_api` checks looked at.
pub fn validate_record_key(name: &str, value: &str) -> Result<()> {
    if value.trim().is_empty() {
        return Err(BcliError::validation(format!(
            "Invalid {name}: must not be empty."
        )));
    }
    if let Some(ch) = ['/', '\\', '?', '#']
        .into_iter()
        .find(|c| value.contains(*c))
    {
        return Err(BcliError::validation(format!(
            "Invalid {name} '{value}': must not contain '{ch}'. A key is a single URL path \
             component — percent-encode the character if it is genuinely part of the key."
        )));
    }
    if matches!(value.trim(), "." | "..") {
        return Err(BcliError::validation(format!(
            "Invalid {name} '{value}': '.' and '..' are path-traversal segments."
        )));
    }
    Ok(())
}

fn api_path(route: Option<&ApiRoute>) -> Result<String> {
    match route {
        Some(r) => {
            validate_route_segment("publisher", &r.publisher)?;
            validate_route_segment("group", &r.group)?;
            validate_route_segment("version", &r.version)?;
            Ok(format!("api/{}/{}/{}", r.publisher, r.group, r.version))
        }
        None => Ok(BC_STANDARD_API_PATH.into()),
    }
}

pub fn build_url(
    urls: &ServiceUrls,
    environment: &str,
    company_id: &str,
    entity_set_name: &str,
    record_id: Option<&str>,
    route: Option<&ApiRoute>,
) -> Result<String> {
    validate_record_key("entity_set_name", entity_set_name)?;
    if let Some(id) = record_id {
        validate_record_key("record_id", id)?;
    }
    let mut url = format!(
        "{}/{environment}/{}/companies({company_id})/{entity_set_name}",
        urls.bc_base,
        api_path(route)?
    );
    if let Some(id) = record_id {
        url.push_str(&format!("({id})"));
    }
    Ok(url)
}

pub fn build_companies_url(urls: &ServiceUrls, environment: &str) -> String {
    format!(
        "{}/{environment}/{BC_STANDARD_API_PATH}/companies",
        urls.bc_base
    )
}

pub fn build_environments_url(urls: &ServiceUrls) -> String {
    format!(
        "{}/applications/businesscentral/environments",
        urls.bc_admin_base
    )
}

/// True for relative URLs and `https` URLs on a Business Central host.
pub fn is_bc_origin(url: &str) -> bool {
    let Some((scheme, rest)) = url.split_once("://") else {
        return true;
    };
    if !scheme.eq_ignore_ascii_case("https") {
        return false;
    }
    let authority = rest.split(['/', '?', '#']).next().unwrap_or("");
    let host_port = authority.rsplit('@').next().unwrap_or("");
    let host = host_port.split(':').next().unwrap_or("").to_lowercase();
    if host.is_empty() {
        return false;
    }
    ALLOWED_HOST_SUFFIXES
        .iter()
        .any(|suffix| host == *suffix || host.ends_with(&format!(".{suffix}")))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn builds_standard_and_custom_urls() {
        let urls = ServiceUrls::default();
        assert_eq!(
            build_url(&urls, "Production", "c1", "customers", None, None).unwrap(),
            "https://api.businesscentral.dynamics.com/v2.0/Production/api/v2.0/companies(c1)/customers"
        );
        let route = ApiRoute {
            publisher: "acme".into(),
            group: "ops".into(),
            version: "v1.0".into(),
        };
        assert_eq!(
            build_url(&urls, "Sandbox", "c1", "widgets", Some("'W 1'"), Some(&route)).unwrap(),
            "https://api.businesscentral.dynamics.com/v2.0/Sandbox/api/acme/ops/v1.0/companies(c1)/widgets('W 1')"
        );
    }

    #[test]
    fn rejects_path_injection() {
        let urls = ServiceUrls::default();
        assert!(build_url(&urls, "P", "c", "foo(1)/../bar", None, None).is_err());
        assert!(build_url(&urls, "P", "c", "foo", Some(""), None).is_err());
        assert!(build_url(&urls, "P", "c", "foo", Some("a?b"), None).is_err());
        let bad = ApiRoute {
            publisher: "..".into(),
            group: "g".into(),
            version: "v".into(),
        };
        assert!(build_url(&urls, "P", "c", "foo", None, Some(&bad)).is_err());
    }

    #[test]
    fn origin_allowlist() {
        assert!(is_bc_origin("/relative?x=1"));
        assert!(is_bc_origin(
            "https://api.businesscentral.dynamics.com/v2.0/x"
        ));
        assert!(is_bc_origin("https://eu.api.bc.dynamics.com/x"));
        assert!(!is_bc_origin("http://api.businesscentral.dynamics.com/x"));
        assert!(!is_bc_origin(
            "https://evilbusinesscentral.dynamics.com.attacker.example/x"
        ));
        assert!(!is_bc_origin(
            "https://attacker.example/?h=api.businesscentral.dynamics.com"
        ));
        assert!(!is_bc_origin(
            "https://api.businesscentral.dynamics.com@attacker.example/"
        ));
    }
}
