//! Transport + client-credentials behaviour against a local mock server.

use std::time::Duration;

use bcli_core::auth::SecretStore;
use bcli_core::client::BcClient;
use bcli_core::config::Profile;
use bcli_core::error::{ErrorKind, EXIT_AUTH, EXIT_REMOTE_5XX};
use bcli_core::paths::Paths;
use bcli_core::url::ServiceUrls;
use serde_json::json;
use wiremock::matchers::{body_string_contains, header, method, path};
use wiremock::{Mock, MockServer, ResponseTemplate};

struct FakeKeyring(Option<&'static str>);

impl SecretStore for FakeKeyring {
    fn get(&self, service: &str, user: &str) -> Option<String> {
        assert_eq!(service, "bcli");
        assert_eq!(user, "tenant-1:client-1");
        self.0.map(str::to_owned)
    }
}

fn profile() -> Profile {
    toml::from_str(
        r#"
tenant_id = "tenant-1"
environment = "Production"
client_id = "client-1"
client_secret_env = "MY_SECRET"
"#,
    )
    .unwrap()
}

fn urls(server: &MockServer) -> ServiceUrls {
    ServiceUrls {
        bc_base: format!("{}/v2.0", server.uri()),
        bc_admin_base: format!("{}/admin/v2.1", server.uri()),
        authority_base: server.uri(),
    }
}

async fn mount_token(server: &MockServer) {
    Mock::given(method("POST"))
        .and(path("/tenant-1/oauth2/v2.0/token"))
        .and(body_string_contains("grant_type=client_credentials"))
        .and(body_string_contains("client_secret=from-keyring"))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({
            "access_token": "tok-123", "expires_in": 3599, "token_type": "Bearer"
        })))
        .expect(1)
        .mount(server)
        .await;
}

#[tokio::test]
async fn client_credentials_then_cached_token() {
    let server = MockServer::start().await;
    mount_token(&server).await;
    Mock::given(method("GET"))
        .and(path("/v2.0/Production/api/v2.0/companies"))
        .and(header("authorization", "Bearer tok-123"))
        .and(header("odata-version", "4.0"))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({
            "value": [{"id": "c1", "name": "Contoso"}]
        })))
        .expect(2)
        .mount(&server)
        .await;

    let home = tempfile::tempdir().unwrap();
    let paths = Paths::from_home(home.path());
    let urls = urls(&server);
    let keyring = FakeKeyring(Some("from-keyring"));
    let env = |_: &str| None;

    for _ in 0..2 {
        let mut client = BcClient::new(
            profile(),
            &paths,
            &urls,
            &keyring,
            &env,
            Duration::from_secs(5),
        )
        .unwrap();
        let companies = client.list_companies().await.unwrap();
        assert_eq!(companies[0]["name"], "Contoso");
    }
    let cache = std::fs::read_to_string(paths.token_cache_file()).unwrap();
    assert!(cache.contains("\"tenant-1:client-1\""));
}

#[tokio::test]
async fn missing_secret_is_a_config_error() {
    let server = MockServer::start().await;
    let home = tempfile::tempdir().unwrap();
    let paths = Paths::from_home(home.path());
    let urls = urls(&server);
    let env = |_: &str| None;
    let mut client = BcClient::new(
        profile(),
        &paths,
        &urls,
        &FakeKeyring(None),
        &env,
        Duration::from_secs(5),
    )
    .unwrap();
    let err = client.list_companies().await.unwrap_err();
    assert_eq!(err.kind, ErrorKind::Config);
    assert!(err.to_string().contains("export MY_SECRET=<secret>"));
}

#[tokio::test]
async fn token_endpoint_error_maps_to_auth() {
    let server = MockServer::start().await;
    Mock::given(method("POST"))
        .respond_with(ResponseTemplate::new(401).set_body_json(json!({
            "error": "invalid_client", "error_description": "AADSTS7000215: Invalid client secret"
        })))
        .mount(&server)
        .await;
    let home = tempfile::tempdir().unwrap();
    let paths = Paths::from_home(home.path());
    let urls = urls(&server);
    let env = |k: &str| (k == "MY_SECRET").then(|| "s".to_string());
    let mut client = BcClient::new(
        profile(),
        &paths,
        &urls,
        &FakeKeyring(None),
        &env,
        Duration::from_secs(5),
    )
    .unwrap();
    let err = client.list_companies().await.unwrap_err();
    assert_eq!(err.exit_code(), EXIT_AUTH);
    assert_eq!(
        err.to_string(),
        "Failed to acquire token: AADSTS7000215: Invalid client secret"
    );
}

#[tokio::test]
async fn retries_503_then_surfaces_bc_error() {
    let server = MockServer::start().await;
    mount_token(&server).await;
    Mock::given(method("GET"))
        .respond_with(
            ResponseTemplate::new(503)
                .insert_header("x-ms-correlation-request-id", "corr-9")
                .set_body_json(
                    json!({"error": {"code": "Unavailable", "message": "Service down"}}),
                ),
        )
        .expect(3)
        .mount(&server)
        .await;
    let home = tempfile::tempdir().unwrap();
    let paths = Paths::from_home(home.path());
    let urls = urls(&server);
    let keyring = FakeKeyring(Some("from-keyring"));
    let env = |_: &str| None;
    let mut client = BcClient::new(
        profile(),
        &paths,
        &urls,
        &keyring,
        &env,
        Duration::from_secs(5),
    )
    .unwrap();
    client.transport.max_retries = 2;
    client.transport.initial_backoff = Duration::from_millis(1);

    let err = client.list_companies().await.unwrap_err();
    assert_eq!(err.kind, ErrorKind::Server);
    assert_eq!(err.exit_code(), EXIT_REMOTE_5XX);
    let text = err.to_string();
    assert!(
        text.starts_with("HTTP 503 Service Unavailable: GET "),
        "{text}"
    );
    assert!(
        text.ends_with(" | BC says: Service down | Correlation ID: corr-9"),
        "{text}"
    );
}
