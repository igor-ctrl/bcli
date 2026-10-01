//! `bcli company list` end to end: argv → config → client-credentials token
//! → BC companies request → formatted output, against a local mock of Entra
//! and Business Central.

use std::collections::HashMap;
use std::path::Path;

use bcli_cli::{run, Env, Io};
use bcli_core::auth::NoSecretStore;
use bcli_core::url::ServiceUrls;
use serde_json::{json, Value};
use wiremock::matchers::{header, method, path};
use wiremock::{Mock, MockServer, ResponseTemplate};

const CONFIG: &str = r#"
[defaults]
profile = "prod"

[profiles.prod]
tenant_id = "tenant-1"
environment = "Production"
company_id = "c-2"
auth_method = "client_credentials"
client_id = "client-1"
client_secret_env = "BC_SECRET"

[profiles.prod.companies.LLC]
id = "c-1"
name = "Contoso LLC"
"#;

struct Run {
    code: i32,
    stdout: String,
    stderr: String,
}

async fn bcli(server: &MockServer, home: &Path, args: &[&str]) -> Run {
    let vars: HashMap<String, String> = [("BC_SECRET".to_string(), "s3cret".to_string())].into();
    let env = Env {
        home: home.to_path_buf(),
        cwd: None,
        vars: Box::new(move |k| vars.get(k).cloned()),
        urls: ServiceUrls {
            bc_base: format!("{}/v2.0", server.uri()),
            bc_admin_base: format!("{}/admin/v2.1", server.uri()),
            authority_base: server.uri(),
        },
        secrets: Box::new(NoSecretStore),
    };
    let (mut out, mut err) = (Vec::new(), Vec::new());
    let mut io = Io::new(&mut out, &mut err, &*env.vars);
    let argv = std::iter::once("bcli")
        .chain(args.iter().copied())
        .map(Into::into)
        .collect();
    let code = run(argv, &env, &mut io).await;
    Run {
        code,
        stdout: String::from_utf8(out).unwrap(),
        stderr: String::from_utf8(err).unwrap(),
    }
}

async fn server_with_companies() -> MockServer {
    let server = MockServer::start().await;
    Mock::given(method("POST"))
        .and(path("/tenant-1/oauth2/v2.0/token"))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({
            "access_token": "tok", "expires_in": 3600
        })))
        .mount(&server)
        .await;
    Mock::given(method("GET"))
        .and(path("/v2.0/Sandbox/api/v2.0/companies"))
        .and(header("authorization", "Bearer tok"))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({
            "@odata.context": "ctx",
            "value": [
                {"id": "c-1", "systemVersion": "1", "name": "Contoso LLC", "displayName": "Contoso"},
                {"id": "c-2", "systemVersion": "1", "name": "Contoso Ltd", "displayName": "Ltd"}
            ]
        })))
        .mount(&server)
        .await;
    server
}

fn home() -> tempfile::TempDir {
    let home = tempfile::tempdir().unwrap();
    let dir = home.path().join(".config").join("bcli");
    std::fs::create_dir_all(&dir).unwrap();
    std::fs::write(dir.join("config.toml"), CONFIG).unwrap();
    home
}

#[tokio::test]
async fn json_rows_follow_the_mcp_contract() {
    let server = server_with_companies().await;
    let home = home();
    let r = bcli(
        &server,
        home.path(),
        &["-e", "Sandbox", "company", "list", "-f", "json"],
    )
    .await;

    assert_eq!(r.code, 0, "{}", r.stderr);
    assert_eq!(r.stderr, "", "json output suppresses the banner");
    let rows: Value = serde_json::from_str(&r.stdout).unwrap();
    assert_eq!(
        rows,
        json!([
            {"id": "c-1", "name": "Contoso LLC", "alias": "LLC", "is_default": false},
            {"id": "c-2", "name": "Contoso Ltd", "alias": null, "is_default": true}
        ])
    );
    assert!(
        r.stdout.starts_with("[\n  {\n    \"id\": \"c-1\","),
        "Python indent=2 layout"
    );
    assert!(
        home.path().join(".config/bcli/tokens.json").is_file(),
        "token cached for the next call"
    );
}

#[tokio::test]
async fn markdown_and_banner_with_company_override() {
    let server = server_with_companies().await;
    let home = home();
    let r = bcli(
        &server,
        home.path(),
        &[
            "-e", "Sandbox", "-c", "LLC", "-f", "table", "company", "list",
        ],
    )
    .await;

    assert_eq!(r.code, 0, "{}", r.stderr);
    assert_eq!(
        r.stderr,
        "[profile: prod | env: Sandbox | company: Contoso LLC]\n"
    );
    assert!(
        r.stdout.contains("Contoso LLC ◄"),
        "-c LLC makes c-1 the default:\n{}",
        r.stdout
    );
    assert!(r.stdout.ends_with("2 company(ies)\n"));

    let md = bcli(
        &server,
        home.path(),
        &["-e", "Sandbox", "company", "list", "-f", "markdown"],
    )
    .await;
    assert_eq!(
        md.stdout,
        "| id  | name        | alias | is_default |\n\
         | --- | ----------- | ----- | ---------- |\n\
         | c-1 | Contoso LLC | LLC   | false      |\n\
         | c-2 | Contoso Ltd |       | true       |\n"
    );
    assert_eq!(md.stderr, "2 record(s)\n");
}

#[tokio::test]
async fn http_failure_reports_on_stdout_with_exit_1() {
    let server = MockServer::start().await;
    Mock::given(method("POST"))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({"access_token": "tok"})))
        .mount(&server)
        .await;
    Mock::given(method("GET"))
        .respond_with(ResponseTemplate::new(403).set_body_json(json!({
            "error": {"code": "Authorization_InsufficientPermissions", "message": "No access"}
        })))
        .mount(&server)
        .await;
    let home = home();
    let r = bcli(&server, home.path(), &["company", "list", "-f", "json"]).await;

    assert_eq!(r.code, 1);
    assert!(
        r.stdout.starts_with("Error: HTTP 403 Forbidden: GET ")
            && r.stdout.ends_with(" | BC says: No access\n"),
        "{}",
        r.stdout
    );
}

#[tokio::test]
async fn unknown_profile_goes_through_the_central_handler() {
    let server = MockServer::start().await;
    let home = home();
    let r = bcli(
        &server,
        home.path(),
        &["-p", "prdo", "-f", "table", "company", "list"],
    )
    .await;
    assert_eq!(r.code, 2);
    assert!(r
        .stderr
        .starts_with("Error: Profile 'prdo' not found. Available: prod."));
    assert!(
        r.stderr.ends_with("\n  Did you mean: prod?\n"),
        "{}",
        r.stderr
    );
}
