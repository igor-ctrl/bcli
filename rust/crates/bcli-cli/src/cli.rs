//! clap command tree mirroring the Typer app in `src/bcli_cli/app.py`.
//!
//! Every command, positional and option of the Python CLI is declared here,
//! including the ones whose handlers are not ported yet, so scripts and the
//! installer see the same surface. `tests/surface_parity.rs` diffs this tree
//! against a dump of the Python CLI.
//!
//! Root options are only accepted before the subcommand (as with Typer);
//! subcommands that take `-f/--format` or `-p/--profile` declare their own.

// Args of not-yet-ported commands are parsed but unread, and these enums are
// built once per process, so variant size is irrelevant.
#![allow(dead_code, clippy::large_enum_variant)]

use std::path::PathBuf;

use clap::{ArgAction, Args, Parser, Subcommand};

const ABOUT: &str = "CLI for Microsoft Dynamics 365 Business Central APIs.";
const LONG_ABOUT: &str = "CLI for Microsoft Dynamics 365 Business Central APIs.

Discovery (handy for AI agents driving bcli):
  bcli endpoint search <pattern>      fuzzy-find an endpoint
  bcli endpoint info <name> -f json   structured metadata
  bcli endpoint fields <name>         discover real field names (don't guess)
  --profile <name> alone is enough — environment, company, and
  client_id resolve from the profile. Pass -e only to override.";

const ESCAPE_HATCH: &str = "escape hatch — registry resolves this automatically";

#[derive(Debug, Parser)]
#[command(
    name = "bcli",
    about = ABOUT,
    long_about = LONG_ABOUT,
    version = bcli_core::VERSION,
    disable_version_flag = true,
    disable_help_subcommand = true,
    arg_required_else_help = true
)]
pub struct Cli {
    #[command(flatten)]
    pub global: GlobalArgs,

    #[command(subcommand)]
    pub command: Command,
}

#[derive(Debug, Clone, Args)]
pub struct GlobalArgs {
    /// Connection profile name
    #[arg(short = 'p', long)]
    pub profile: Option<String>,
    /// Override environment name
    #[arg(short = 'e', long)]
    pub env: Option<String>,
    /// Override company ID
    #[arg(short = 'c', long)]
    pub company: Option<String>,
    /// Output format: table, markdown, json, csv, ndjson, raw (auto-detects for non-TTY/AI agents)
    #[arg(short = 'f', long)]
    pub format: Option<String>,
    /// Show resolved URLs and timing
    #[arg(short = 'v', long)]
    pub verbose: bool,
    /// Show full HTTP request/response
    #[arg(long)]
    pub debug: bool,
    /// Show what would execute
    #[arg(long)]
    pub dry_run: bool,
    /// Suppress context banner
    #[arg(short = 'q', long)]
    pub quiet: bool,
    /// Print version
    #[arg(short = 'V', long, action = ArgAction::Version)]
    version: Option<bool>,
}

#[derive(Debug, Subcommand)]
pub enum Command {
    /// Configuration management
    #[command(subcommand, arg_required_else_help = true)]
    Config(ConfigCmd),
    /// Authentication
    #[command(subcommand, arg_required_else_help = true)]
    Auth(AuthCmd),
    /// Environment discovery and selection
    #[command(subcommand, arg_required_else_help = true)]
    Env(EnvCmd),
    /// Company discovery and selection
    #[command(subcommand, arg_required_else_help = true)]
    Company(CompanyCmd),
    /// Endpoint discovery
    #[command(subcommand, arg_required_else_help = true)]
    Endpoint(EndpointCmd),
    /// Custom API registry management
    #[command(subcommand, arg_required_else_help = true)]
    Registry(RegistryCmd),
    /// Connection and endpoint testing
    #[command(subcommand, arg_required_else_help = true)]
    Test(TestCmd),
    /// Batch operations from YAML files
    #[command(subcommand, arg_required_else_help = true)]
    Batch(BatchCmd),
    /// Document-attachment workflows (two-phase /attachments upload)
    #[command(subcommand, arg_required_else_help = true)]
    Attach(AttachCmd),
    /// Generate a per-user bcli skill bundle
    #[command(subcommand, arg_required_else_help = true)]
    Skill(SkillCmd),
    /// Install reusable query/batch/fragment packs
    #[command(subcommand, arg_required_else_help = true)]
    Pack(PackCmd),
    /// ETL pipeline (requires dlt)
    #[command(subcommand, arg_required_else_help = true)]
    Etl(EtlCmd),
    /// PDF → batch.yaml via AI vision
    #[command(subcommand, arg_required_else_help = true)]
    Extract(ExtractCmd),

    /// Query records from an endpoint
    Get(GetArgs),
    /// Create a record
    Post(PostArgs),
    /// Update a record
    Patch(PatchArgs),
    /// Delete a record
    Delete(DeleteArgs),
    /// Invoke an OData v4 bound action on a record
    Action(ActionArgs),
    /// Run a saved query (no OData required)
    Q(QueryArgs),
    /// Print a redacted context bundle for an LLM
    AiContext,
    /// Ask an LLM oracle about your recent bcli context
    Ask(AskArgs),
    /// Diagnose your bcli install (self-rescue for team users)
    Doctor(DoctorArgs),
    /// Project the CLI surface + registry + profile as JSON for agents
    Describe(DescribeArgs),
}

impl Command {
    /// Space-separated command path, as used in messages and telemetry.
    pub fn path(&self) -> String {
        let (group, sub) = match self {
            Command::Config(c) => ("config", c.name()),
            Command::Auth(c) => ("auth", c.name()),
            Command::Env(c) => ("env", c.name()),
            Command::Company(c) => ("company", c.name()),
            Command::Endpoint(c) => ("endpoint", c.name()),
            Command::Registry(c) => ("registry", c.name()),
            Command::Test(c) => ("test", c.name()),
            Command::Batch(c) => ("batch", c.name()),
            Command::Attach(c) => ("attach", c.name()),
            Command::Skill(c) => ("skill", c.name()),
            Command::Pack(c) => ("pack", c.name()),
            Command::Etl(c) => ("etl", c.name()),
            Command::Extract(c) => ("extract", c.name()),
            Command::Get(_) => ("get", ""),
            Command::Post(_) => ("post", ""),
            Command::Patch(_) => ("patch", ""),
            Command::Delete(_) => ("delete", ""),
            Command::Action(_) => ("action", ""),
            Command::Q(_) => ("q", ""),
            Command::AiContext => ("ai-context", ""),
            Command::Ask(_) => ("ask", ""),
            Command::Doctor(_) => ("doctor", ""),
            Command::Describe(_) => ("describe", ""),
        };
        if sub.is_empty() {
            group.to_string()
        } else {
            format!("{group} {sub}")
        }
    }
}

/// Subcommand name in kebab-case, derived from the variant's `Debug` name.
macro_rules! named {
    ($ty:ty { $($variant:ident => $name:literal),+ $(,)? }) => {
        impl $ty {
            pub fn name(&self) -> &'static str {
                match self { $(Self::$variant { .. } => $name),+ }
            }
        }
    };
}

// ─── config ─────────────────────────────────────────────────────────

#[derive(Debug, Subcommand)]
pub enum ConfigCmd {
    /// Interactive first-run setup
    Init {
        #[arg(short = 'p', long)]
        profile: Option<String>,
        #[arg(long)]
        auth: Option<String>,
        #[arg(long)]
        automation: bool,
        #[arg(long)]
        headless: bool,
        #[arg(long)]
        scoped: bool,
        #[arg(long)]
        category: Vec<String>,
        #[arg(long = "import")]
        import_file: Option<PathBuf>,
    },
    /// Show the resolved configuration
    Show,
    /// Set a configuration value
    Set { key: String, value: String },
    /// Set the default profile
    Use { name: String },
    /// Print the config file path
    Path,
    /// Open the config file in $EDITOR
    Edit,
    /// Pull the latest team bundle for a profile
    Refresh {
        #[arg(short = 'p', long)]
        profile: Option<String>,
        #[arg(long)]
        url: Option<String>,
        #[arg(long)]
        dry_run: bool,
        #[arg(long)]
        skip_verify: bool,
    },
    /// Restore the previous bundle for a profile
    Rollback {
        #[arg(short = 'p', long)]
        profile: Option<String>,
    },
    /// Show the currently-installed bundle's manifest
    BundleStatus {
        #[arg(short = 'p', long)]
        profile: Option<String>,
    },
    /// Build a bundle tarball from a directory (admin)
    MakeBundle {
        source_dir: PathBuf,
        #[arg(short = 'p', long, required = true)]
        profile: String,
        #[arg(long, required = true)]
        version: String,
        #[arg(long)]
        publisher: Option<String>,
        #[arg(long)]
        notes: Option<String>,
        #[arg(long)]
        previous: Option<String>,
        #[arg(short = 'o', long)]
        output: Option<PathBuf>,
    },
}
named!(ConfigCmd { Init => "init", Show => "show", Set => "set", Use => "use", Path => "path",
    Edit => "edit", Refresh => "refresh", Rollback => "rollback", BundleStatus => "bundle-status",
    MakeBundle => "make-bundle" });

// ─── auth ───────────────────────────────────────────────────────────

#[derive(Debug, Subcommand)]
pub enum AuthCmd {
    /// Sign in (browser, device_code, or client_credentials)
    Login {
        #[arg(short = 'm', long)]
        method: Option<String>,
        #[arg(short = 'i', long)]
        incognito: bool,
    },
    /// Show token status for the active profile
    Status,
    /// Clear cached tokens for the active profile
    Logout,
    /// Save the client secret to the OS keychain
    StoreSecret,
    /// Remove the client secret from the OS keychain
    DeleteSecret,
}
named!(AuthCmd { Login => "login", Status => "status", Logout => "logout",
    StoreSecret => "store-secret", DeleteSecret => "delete-secret" });

// ─── env / company / endpoint / registry / test ─────────────────────

#[derive(Debug, Subcommand)]
pub enum EnvCmd {
    /// List available Business Central environments
    List,
    /// Set the default environment for the active profile
    Use { name: String },
}
named!(EnvCmd { List => "list", Use => "use" });

#[derive(Debug, Subcommand)]
pub enum CompanyCmd {
    /// List all companies in the current environment
    List(FormatArg),
    /// Set the default company for the active profile
    Use {
        /// Company ID (GUID) or alias
        company: String,
    },
    /// Assign a nickname to a company for quick access
    Alias {
        name: String,
        company_id: String,
        #[arg(short = 'n', long = "name")]
        display_name: Option<String>,
    },
    /// Show all company aliases for the active profile
    Aliases,
    /// Copy company aliases from another profile into the active profile
    AliasesImport {
        #[arg(long = "from", required = true)]
        from_profile: String,
        #[arg(long)]
        overwrite: bool,
        #[arg(long)]
        dry_run: bool,
    },
}
named!(CompanyCmd { List => "list", Use => "use", Alias => "alias", Aliases => "aliases",
    AliasesImport => "aliases-import" });

#[derive(Debug, Clone, Args)]
pub struct FormatArg {
    /// Output format: table (default), json, markdown, csv, ndjson
    #[arg(short = 'f', long)]
    pub format: Option<String>,
}

#[derive(Debug, Subcommand)]
pub enum EndpointCmd {
    /// List all known endpoints (standard + custom)
    List {
        /// Show only custom (imported) endpoints
        #[arg(long)]
        custom: bool,
        /// Show only standard v2.0 endpoints
        #[arg(long)]
        standard: bool,
        /// Filter by category
        #[arg(long)]
        category: Option<String>,
        #[command(flatten)]
        format: FormatArg,
    },
    /// Fuzzy search endpoints by name or description
    Search {
        /// Search term
        query: String,
    },
    /// Show detailed metadata for an endpoint
    Info {
        /// Entity set name
        name: String,
        #[command(flatten)]
        format: FormatArg,
    },
    /// Discover field names and types by fetching one record from the API
    Fields {
        /// Entity set name
        name: String,
    },
}
named!(EndpointCmd { List => "list", Search => "search", Info => "info", Fields => "fields" });

#[derive(Debug, Subcommand)]
pub enum RegistryCmd {
    /// Import custom API endpoints
    Import {
        #[arg(long)]
        from_postman: Option<PathBuf>,
        #[arg(long)]
        from_json: Option<PathBuf>,
        #[arg(long)]
        from_metadata: bool,
        #[arg(short = 'p', long)]
        profile: Option<String>,
    },
    /// List registries
    List,
}
named!(RegistryCmd { Import => "import", List => "list" });

#[derive(Debug, Subcommand)]
pub enum TestCmd {
    /// Test API connectivity
    Connection,
    /// Test token acquisition
    Auth,
    /// Test a single endpoint
    Endpoint { name: String },
}
named!(TestCmd { Connection => "connection", Auth => "auth", Endpoint => "endpoint" });

// ─── batch / attach / skill / pack / etl / extract ──────────────────

#[derive(Debug, Clone, Args)]
pub struct ResultArgs {
    /// Write the JSON result envelope to this path
    #[arg(long)]
    pub result_out: Option<PathBuf>,
    /// Write the JSON result envelope to this file descriptor
    #[arg(long)]
    pub result_fd: Option<i32>,
}

#[derive(Debug, Subcommand)]
pub enum BatchCmd {
    /// Run a batch YAML file
    Run {
        file: PathBuf,
        #[arg(long)]
        dry_run: bool,
        #[arg(short = 'o', long)]
        output: Option<PathBuf>,
        #[arg(short = 'f', long)]
        format: Option<String>,
        #[arg(long = "set")]
        set: Vec<String>,
        #[arg(long)]
        params: Option<PathBuf>,
        #[arg(short = 'y', long)]
        yes: bool,
        #[command(flatten)]
        result: ResultArgs,
        #[arg(long)]
        progress_fd: Option<i32>,
    },
    /// Per-step detail for one run
    State {
        run_id: String,
        #[arg(short = 'f', long)]
        format: Option<String>,
    },
    /// Recent runs, newest first
    List {
        #[arg(long)]
        state: Option<String>,
        #[arg(long)]
        limit: Option<u32>,
        #[arg(short = 'f', long)]
        format: Option<String>,
    },
    /// Undo a run (POST → DELETE only)
    Rollback {
        run_id: String,
        #[arg(long)]
        dry_run: bool,
        #[arg(short = 'y', long)]
        yes: bool,
    },
}
named!(BatchCmd { Run => "run", State => "state", List => "list", Rollback => "rollback" });

#[derive(Debug, Clone, Args)]
pub struct AttachCommon {
    #[arg(long)]
    pub file_name: Option<String>,
    #[arg(long)]
    pub content_type: Option<String>,
    #[command(flatten)]
    pub route: HiddenRouteArgs,
    /// Use the standard v2.0 /attachments route instead of the registry
    #[arg(long = "no-registry", visible_alias = "standard")]
    pub no_registry: bool,
    #[arg(short = 'f', long)]
    pub format: Option<String>,
}

#[derive(Debug, Subcommand)]
pub enum AttachCmd {
    /// Two-phase upload of a document to a parent record
    Upload {
        file_path: PathBuf,
        #[arg(long, required = true)]
        parent_id: String,
        #[arg(long)]
        parent_type: Option<String>,
        #[command(flatten)]
        common: AttachCommon,
        #[arg(short = 'y', long)]
        yes: bool,
        #[command(flatten)]
        result: ResultArgs,
        #[arg(long)]
        idempotency_key: Option<String>,
    },
    /// Upload against a throwaway purchase invoice to verify the flow
    Test {
        file_path: PathBuf,
        #[arg(long, required = true)]
        vendor_id: String,
        #[arg(long)]
        invoice_date: Option<String>,
        #[command(flatten)]
        common: AttachCommon,
    },
}
named!(AttachCmd { Upload => "upload", Test => "test" });

#[derive(Debug, Subcommand)]
pub enum SkillCmd {
    /// Generate a per-user skill bundle
    Init {
        #[arg(long)]
        profile: Option<String>,
        #[arg(long)]
        target_skills_dir: Option<PathBuf>,
        #[arg(long)]
        non_interactive: bool,
    },
    /// Regenerate the skill bundle
    Update {
        #[arg(long)]
        profile: Option<String>,
        #[arg(long)]
        non_interactive: bool,
    },
    /// Project the skill bundle into an agent's skills directory
    Install {
        #[arg(short = 't', long)]
        target: Option<String>,
        #[arg(long)]
        dry_run: bool,
    },
}
named!(SkillCmd { Init => "init", Update => "update", Install => "install" });

#[derive(Debug, Subcommand)]
pub enum PackCmd {
    /// List available packs
    List {
        #[arg(short = 'p', long)]
        profile: Option<String>,
    },
    /// Show a pack's contents
    Info {
        name: String,
        #[arg(short = 'p', long)]
        profile: Option<String>,
    },
    /// Install a pack
    Install {
        name: String,
        #[arg(short = 'p', long)]
        profile: Option<String>,
        #[arg(long)]
        target: Option<String>,
        #[arg(long)]
        dry_run: bool,
        #[arg(long)]
        replace_owned: bool,
        #[arg(long)]
        accept_conflicts: bool,
        #[arg(short = 'y', long)]
        yes: bool,
    },
    /// Remove an installed pack
    Uninstall {
        name: String,
        #[arg(short = 'p', long)]
        profile: Option<String>,
        #[arg(short = 'y', long)]
        yes: bool,
    },
}
named!(PackCmd { List => "list", Info => "info", Install => "install", Uninstall => "uninstall" });

#[derive(Debug, Subcommand)]
pub enum EtlCmd {
    /// List entities available for sync
    Entities {
        #[arg(long)]
        include_standard: bool,
    },
    /// Run a sync pipeline
    Sync {
        #[arg(long)]
        entities: Option<String>,
        #[arg(short = 'd', long)]
        destination: Option<String>,
        #[arg(long)]
        dataset: Option<String>,
        #[arg(long)]
        pipeline: Option<String>,
        #[arg(long)]
        full_refresh: bool,
        #[arg(long)]
        include_standard: bool,
        #[arg(long)]
        file_format: Option<String>,
        #[arg(long)]
        stamper: Vec<String>,
        #[arg(long)]
        polaris_uri: Option<String>,
        #[arg(long)]
        polaris_warehouse: Option<String>,
        #[arg(long)]
        polaris_credential: Option<String>,
        #[arg(long)]
        polaris_namespace: Option<String>,
    },
}
named!(EtlCmd { Entities => "entities", Sync => "sync" });

#[derive(Debug, Subcommand)]
pub enum ExtractCmd {
    /// Extract a PDF into a batch.yaml
    Run {
        pdf_path: PathBuf,
        #[arg(short = 's', long, required = true)]
        schema: String,
        #[arg(short = 'o', long)]
        output: Option<PathBuf>,
        #[arg(long)]
        sidecar: Option<PathBuf>,
        #[arg(long)]
        overwrite: bool,
        #[arg(long)]
        progress_fd: Option<i32>,
    },
    /// List extraction schemas
    ListSchemas,
}
named!(ExtractCmd { Run => "run", ListSchemas => "list-schemas" });

// ─── top-level verbs ────────────────────────────────────────────────

#[derive(Debug, Clone, Args)]
pub struct RouteArgs {
    #[arg(long, help = format!("Custom API publisher override ({ESCAPE_HATCH})"))]
    pub publisher: Option<String>,
    #[arg(long, help = format!("Custom API group override ({ESCAPE_HATCH})"))]
    pub group: Option<String>,
    #[arg(long, help = format!("Custom API version override ({ESCAPE_HATCH})"))]
    pub version: Option<String>,
}

#[derive(Debug, Clone, Args)]
pub struct HiddenRouteArgs {
    #[arg(long, hide = true)]
    pub publisher: Option<String>,
    #[arg(long, hide = true)]
    pub group: Option<String>,
    #[arg(long, hide = true)]
    pub version: Option<String>,
}

#[derive(Debug, Clone, Args)]
pub struct WriteArgs {
    /// Skip the read-only-profile warning prompt
    #[arg(short = 'y', long)]
    pub yes: bool,
    #[command(flatten)]
    pub result: ResultArgs,
    /// IETF Idempotency-Key header; re-enables safe retries
    #[arg(long)]
    pub idempotency_key: Option<String>,
}

#[derive(Debug, Args)]
pub struct GetArgs {
    /// Entity set name (e.g., 'customers', 'vendors')
    pub endpoint: String,
    /// Record ID for single-record GET
    pub record_id: Option<String>,
    /// OData $filter expression
    #[arg(long)]
    pub filter: Option<String>,
    /// Comma-separated field names
    #[arg(long)]
    pub select: Option<String>,
    /// Comma-separated navigation properties
    #[arg(long)]
    pub expand: Option<String>,
    /// OData $orderby expression
    #[arg(long)]
    pub orderby: Option<String>,
    /// Max records to return
    #[arg(long)]
    pub top: Option<u64>,
    /// Records to skip
    #[arg(long)]
    pub skip: Option<u64>,
    /// Include total record count
    #[arg(long)]
    pub count: bool,
    /// Follow pagination to get all records
    #[arg(long = "all")]
    pub all_pages: bool,
    /// Write the record's media stream (raw bytes) to this path
    #[arg(long)]
    pub out: Option<PathBuf>,
    /// Media property to download
    #[arg(long)]
    pub media: Option<String>,
    /// Replace an existing --out file
    #[arg(long)]
    pub overwrite: bool,
    #[arg(short = 'f', long)]
    pub format: Option<String>,
    #[command(flatten)]
    pub route: RouteArgs,
}

#[derive(Debug, Args)]
pub struct PostArgs {
    /// Entity set name
    pub endpoint: String,
    /// JSON data or @filename
    #[arg(short = 'd', long, required = true)]
    pub data: String,
    #[arg(short = 'f', long)]
    pub format: Option<String>,
    #[command(flatten)]
    pub route: RouteArgs,
    #[command(flatten)]
    pub write: WriteArgs,
}

#[derive(Debug, Args)]
pub struct PatchArgs {
    /// Entity set name
    pub endpoint: String,
    /// Record ID to update
    pub record_id: String,
    /// JSON data or @filename
    #[arg(short = 'd', long, required = true)]
    pub data: String,
    /// ETag for optimistic concurrency
    #[arg(long, default_value = "*")]
    pub etag: String,
    #[arg(short = 'f', long)]
    pub format: Option<String>,
    #[command(flatten)]
    pub route: RouteArgs,
    #[command(flatten)]
    pub write: WriteArgs,
}

#[derive(Debug, Args)]
pub struct DeleteArgs {
    /// Entity set name
    pub endpoint: String,
    /// Record ID to delete
    pub record_id: String,
    /// ETag for optimistic concurrency
    #[arg(long, default_value = "*")]
    pub etag: String,
    #[arg(short = 'f', long)]
    pub format: Option<String>,
    #[command(flatten)]
    pub route: RouteArgs,
    #[command(flatten)]
    pub write: WriteArgs,
}

#[derive(Debug, Args)]
pub struct ActionArgs {
    pub entity_set: String,
    pub key: String,
    pub action_name: String,
    #[arg(short = 'd', long)]
    pub data: Option<String>,
    #[arg(long)]
    pub no_data: bool,
    #[arg(short = 'n', long)]
    pub namespace: Option<String>,
    #[arg(short = 'f', long)]
    pub format: Option<String>,
    #[command(flatten)]
    pub route: HiddenRouteArgs,
    #[arg(long)]
    pub out: Option<PathBuf>,
    #[arg(long)]
    pub overwrite: bool,
    #[command(flatten)]
    pub write: WriteArgs,
}

#[derive(Debug, Args)]
pub struct QueryArgs {
    pub name: Option<String>,
    #[arg(num_args = 0..)]
    pub params: Vec<String>,
    #[arg(long)]
    pub show: bool,
    #[arg(short = 'f', long)]
    pub format: Option<String>,
}

#[derive(Debug, Args)]
pub struct AskArgs {
    pub question: String,
    #[arg(long)]
    pub no_context: bool,
    #[arg(long)]
    pub attach: Vec<PathBuf>,
    #[arg(long)]
    pub backend: Option<String>,
    #[arg(long)]
    pub dry_run: bool,
    #[arg(long)]
    pub include_bodies: bool,
    #[arg(long)]
    pub include_debug: bool,
    #[arg(long)]
    pub max_tokens: Option<u32>,
}

#[derive(Debug, Args)]
pub struct DoctorArgs {
    #[arg(short = 'p', long)]
    pub profile: Option<String>,
    #[arg(long)]
    pub json: bool,
    #[arg(long)]
    pub skip_network: bool,
}

#[derive(Debug, Args)]
pub struct DescribeArgs {
    #[arg(num_args = 0..)]
    pub command_path: Vec<String>,
    #[arg(short = 'f', long)]
    pub format: Option<String>,
}
