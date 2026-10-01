//! Endpoint registry (`bcli.registry`): name → API route resolution.
//!
//! Custom per-profile entries (`~/.config/bcli/registries/<profile>.json`)
//! take priority over the built-in standard v2.0 catalog. Lookups are
//! case-insensitive; allowlists from the profile filter every read.

use std::path::Path;

use indexmap::IndexMap;
use serde::{Deserialize, Serialize};

use crate::error::{BcliError, Result};

/// The standard v2.0 catalog is shared with the Python package rather than
/// copied, so both builds always ship the same list.
const STANDARD_V2_JSON: &str = include_str!("../../../../src/bcli/registry/standard_v2.json");

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Caution {
    #[default]
    Low,
    Medium,
    High,
}

impl Caution {
    pub fn as_str(self) -> &'static str {
        match self {
            Caution::Low => "low",
            Caution::Medium => "medium",
            Caution::High => "high",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct EndpointMetadata {
    pub entity_set_name: String,
    #[serde(default)]
    pub entity_name: String,
    #[serde(default)]
    pub description: String,
    #[serde(default = "default_supports")]
    pub supports: Vec<String>,
    #[serde(default = "default_key_field")]
    pub key_field: String,
    #[serde(default)]
    pub category: String,
    #[serde(default)]
    pub api_publisher: Option<String>,
    #[serde(default)]
    pub api_group: Option<String>,
    #[serde(default)]
    pub api_version: Option<String>,
    #[serde(default = "default_domain")]
    pub domain: String,
    #[serde(default)]
    pub caution: Caution,
    #[serde(default)]
    pub source_table: String,
    #[serde(default)]
    pub page_number: String,
    #[serde(default)]
    pub editable: bool,
    #[serde(default)]
    pub field_names: Vec<String>,
    #[serde(flatten)]
    pub extra: serde_json::Map<String, serde_json::Value>,
}

fn default_supports() -> Vec<String> {
    vec!["GET".into()]
}
fn default_key_field() -> String {
    "id".into()
}
fn default_domain() -> String {
    "standard".into()
}

impl EndpointMetadata {
    pub fn is_custom(&self) -> bool {
        self.api_publisher.is_some()
    }

    pub fn route_display(&self) -> String {
        if self.is_custom() {
            format!(
                "{}/{}/{}",
                self.api_publisher.as_deref().unwrap_or("None"),
                self.api_group.as_deref().unwrap_or("None"),
                self.api_version.as_deref().unwrap_or("None"),
            )
        } else {
            "v2.0 (standard)".into()
        }
    }
}

#[derive(Deserialize)]
struct StandardFile {
    entities: Vec<EndpointMetadata>,
}

#[derive(Deserialize)]
struct CustomFile {
    #[serde(default)]
    endpoints: Vec<EndpointMetadata>,
}

#[derive(Debug, Default, Clone)]
pub struct RegistryOptions {
    pub disable_standard: bool,
    pub allowed_categories: Vec<String>,
    pub allowed_endpoints: Vec<String>,
}

#[derive(Debug, Default)]
pub struct Registry {
    standard: IndexMap<String, EndpointMetadata>,
    custom: IndexMap<String, EndpointMetadata>,
    allowed_categories: Option<Vec<String>>,
    allowed_endpoints: Option<Vec<String>>,
}

impl Registry {
    pub fn load(custom_file: Option<&Path>, opts: &RegistryOptions) -> Result<Self> {
        let lower = |v: &[String]| -> Option<Vec<String>> {
            (!v.is_empty()).then(|| v.iter().map(|s| s.to_lowercase()).collect())
        };
        let mut registry = Registry {
            allowed_categories: lower(&opts.allowed_categories),
            allowed_endpoints: lower(&opts.allowed_endpoints),
            ..Default::default()
        };
        if !opts.disable_standard {
            let parsed: StandardFile = serde_json::from_str(STANDARD_V2_JSON).map_err(|e| {
                BcliError::registry(format!("Bundled standard registry is invalid: {e}"))
            })?;
            registry.standard = index(parsed.entities);
        }
        if let Some(path) = custom_file.filter(|p| p.is_file()) {
            let text = std::fs::read_to_string(path)
                .map_err(|e| BcliError::registry(format!("Cannot read {}: {e}", path.display())))?;
            let parsed: CustomFile = serde_json::from_str(&text).map_err(|e| {
                BcliError::registry(format!("Invalid registry file {}: {e}", path.display()))
            })?;
            registry.custom = index(parsed.endpoints);
        }
        Ok(registry)
    }

    pub fn standard_count(&self) -> usize {
        self.standard.len()
    }

    pub fn custom_count(&self) -> usize {
        self.custom.len()
    }

    fn is_allowed(&self, meta: &EndpointMetadata) -> bool {
        if let Some(allowed) = &self.allowed_endpoints {
            if allowed.contains(&meta.entity_set_name.to_lowercase()) {
                return true;
            }
        }
        match &self.allowed_categories {
            Some(categories) => categories.contains(&meta.category.to_lowercase()),
            None => true,
        }
    }

    pub fn get(&self, name: &str) -> Option<&EndpointMetadata> {
        let key = name.to_lowercase();
        self.custom
            .get(&key)
            .or_else(|| self.standard.get(&key))
            .filter(|meta| self.is_allowed(meta))
    }

    pub fn resolve(&self, name: &str) -> Result<&EndpointMetadata> {
        self.get(name).ok_or_else(|| {
            let suggestions: Vec<&str> = self
                .search(name)
                .into_iter()
                .take(3)
                .map(|m| m.entity_set_name.as_str())
                .collect();
            let hint = if suggestions.is_empty() {
                String::new()
            } else {
                format!(" Did you mean: {}?", suggestions.join(", "))
            };
            BcliError::registry(format!(
                "Endpoint '{name}' not found in any registry.{hint} Run 'bcli registry import' to \
                 add custom APIs, or pass --publisher/--group/--version to target a custom API \
                 route (this does NOT reach Microsoft's standard v2.0 entities)."
            ))
        })
    }

    /// Scored substring search: exact name 100, name 80, description 40, category 30.
    pub fn search(&self, query: &str) -> Vec<&EndpointMetadata> {
        let q = query.to_lowercase();
        let mut scored: Vec<(u8, &EndpointMetadata)> = self
            .custom
            .values()
            .chain(self.standard.values())
            .filter(|meta| self.is_allowed(meta))
            .filter_map(|meta| {
                let name = meta.entity_set_name.to_lowercase();
                let score = if q == name {
                    100
                } else if name.contains(&q) {
                    80
                } else if meta.description.to_lowercase().contains(&q) {
                    40
                } else if meta.category.to_lowercase().contains(&q) {
                    30
                } else {
                    0
                };
                (score > 0).then_some((score, meta))
            })
            .collect();
        scored.sort_by(|a, b| {
            b.0.cmp(&a.0)
                .then_with(|| a.1.entity_set_name.cmp(&b.1.entity_set_name))
        });
        scored.into_iter().map(|(_, meta)| meta).collect()
    }

    pub fn list_all(&self, custom_only: bool, standard_only: bool) -> Vec<&EndpointMetadata> {
        let mut out = Vec::new();
        if !standard_only {
            out.extend(self.sorted_allowed(&self.custom));
        }
        if !custom_only {
            out.extend(self.sorted_allowed(&self.standard));
        }
        out
    }

    fn sorted_allowed<'a>(
        &self,
        map: &'a IndexMap<String, EndpointMetadata>,
    ) -> Vec<&'a EndpointMetadata> {
        let mut v: Vec<&EndpointMetadata> = map.values().filter(|m| self.is_allowed(m)).collect();
        v.sort_by(|a, b| a.entity_set_name.cmp(&b.entity_set_name));
        v
    }
}

fn index(entries: Vec<EndpointMetadata>) -> IndexMap<String, EndpointMetadata> {
    entries
        .into_iter()
        .map(|meta| (meta.entity_set_name.to_lowercase(), meta))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    const CUSTOM: &str = r#"{"endpoints": [
        {"entity_set_name": "widgetLines", "description": "Widget lines", "category": "ops",
         "api_publisher": "acme", "api_group": "ops", "api_version": "v1.0", "supports": ["GET", "PATCH"]},
        {"entity_set_name": "customers", "description": "Custom customers", "category": "sales",
         "api_publisher": "acme", "api_group": "sales", "api_version": "v2.0", "caution": "high"}
    ]}"#;

    fn with_custom(opts: RegistryOptions) -> (tempfile::TempDir, Registry) {
        let dir = tempfile::tempdir().unwrap();
        let file = dir.path().join("p.json");
        std::fs::write(&file, CUSTOM).unwrap();
        let reg = Registry::load(Some(&file), &opts).unwrap();
        (dir, reg)
    }

    #[test]
    fn standard_catalog_loads() {
        let reg = Registry::load(None, &RegistryOptions::default()).unwrap();
        assert!(reg.standard_count() > 50);
        assert_eq!(reg.get("CUSTOMERS").unwrap().entity_set_name, "customers");
        assert!(!reg.get("customers").unwrap().is_custom());
    }

    #[test]
    fn custom_shadows_standard() {
        let (_d, reg) = with_custom(RegistryOptions::default());
        let c = reg.get("customers").unwrap();
        assert!(c.is_custom());
        assert_eq!(c.caution, Caution::High);
        assert_eq!(c.route_display(), "acme/sales/v2.0");
    }

    #[test]
    fn allowlists_filter_reads() {
        let (_d, reg) = with_custom(RegistryOptions {
            disable_standard: true,
            allowed_categories: vec!["OPS".into()],
            allowed_endpoints: vec![],
        });
        assert!(reg.get("customers").is_none());
        assert!(reg.get("widgetlines").is_some());
        assert_eq!(reg.list_all(false, false).len(), 1);
    }

    #[test]
    fn resolve_suggests_close_names() {
        let (_d, reg) = with_custom(RegistryOptions::default());
        let err = reg.resolve("widget").unwrap_err();
        assert!(
            err.to_string().contains("Did you mean: widgetLines?"),
            "{err}"
        );
    }

    #[test]
    fn search_orders_by_score_then_name() {
        let (_d, reg) = with_custom(RegistryOptions::default());
        let names: Vec<_> = reg
            .search("customers")
            .iter()
            .map(|m| m.entity_set_name.clone())
            .collect();
        assert_eq!(names[0], "customers");
    }
}
