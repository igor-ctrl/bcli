//! `tokens.json` access-token cache, shared on disk with the Python build.
//!
//! Format: `{"<tenant>:<client>": {"access_token", "expires_at", "cached_at"}}`
//! with ISO-8601 timestamps (`datetime.isoformat()`), written by
//! `json.dumps(indent=2)`. A token is reused while more than 5 minutes remain.

use std::path::PathBuf;

use chrono::{DateTime, Duration, SecondsFormat, Utc};
use serde_json::{json, Map, Value};

use super::secure_io::{tighten_if_insecure, write_secret_file};
use crate::error::{BcliError, Result};
use crate::pyjson;

const EXPIRY_BUFFER_SECS: i64 = 300;

pub struct TokenCache {
    file: PathBuf,
    data: Option<Map<String, Value>>,
    pub warnings: Vec<String>,
}

impl TokenCache {
    pub fn new(file: PathBuf) -> Self {
        Self {
            file,
            data: None,
            warnings: Vec::new(),
        }
    }

    fn key(tenant_id: &str, client_id: &str) -> String {
        format!("{tenant_id}:{client_id}")
    }

    fn load(&mut self) -> &mut Map<String, Value> {
        if self.data.is_none() {
            let mut data = Map::new();
            if self.file.is_file() {
                self.warnings.extend(tighten_if_insecure(&self.file));
                if let Ok(Value::Object(obj)) = std::fs::read_to_string(&self.file)
                    .map_err(|_| ())
                    .and_then(|s| serde_json::from_str(&s).map_err(|_| ()))
                {
                    data = obj;
                }
            }
            self.data = Some(data);
        }
        self.data.get_or_insert_with(Map::new)
    }

    pub fn get(&mut self, tenant_id: &str, client_id: &str, now: DateTime<Utc>) -> Option<String> {
        let entry = self.load().get(&Self::key(tenant_id, client_id))?;
        let expires_at = DateTime::parse_from_rfc3339(entry.get("expires_at")?.as_str()?).ok()?;
        let remaining = expires_at.with_timezone(&Utc) - now;
        if remaining > Duration::seconds(EXPIRY_BUFFER_SECS) {
            entry.get("access_token")?.as_str().map(str::to_owned)
        } else {
            None
        }
    }

    pub fn put(
        &mut self,
        tenant_id: &str,
        client_id: &str,
        access_token: &str,
        expires_in: i64,
        now: DateTime<Utc>,
    ) -> Result<()> {
        let iso = |t: DateTime<Utc>| t.to_rfc3339_opts(SecondsFormat::Micros, false);
        let entry = json!({
            "access_token": access_token,
            "expires_at": iso(now + Duration::seconds(expires_in)),
            "cached_at": iso(now),
        });
        self.load().insert(Self::key(tenant_id, client_id), entry);
        self.save()
    }

    fn save(&mut self) -> Result<()> {
        let body = pyjson::dumps(&Value::Object(self.load().clone()), Some(2));
        let warnings = write_secret_file(&self.file, &body)
            .map_err(|e| BcliError::auth(format!("Cannot write {}: {e}", self.file.display())))?;
        self.warnings.extend(warnings);
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reads_a_python_written_cache() {
        let dir = tempfile::tempdir().unwrap();
        let file = dir.path().join("tokens.json");
        std::fs::write(
            &file,
            r#"{
  "t:c": {
    "access_token": "tok",
    "expires_at": "2026-09-30T12:00:00.123456+00:00",
    "cached_at": "2026-09-30T11:00:00+00:00"
  }
}"#,
        )
        .unwrap();
        let mut cache = TokenCache::new(file);
        let early = DateTime::parse_from_rfc3339("2026-09-30T11:50:00Z")
            .unwrap()
            .to_utc();
        let late = DateTime::parse_from_rfc3339("2026-09-30T11:56:00Z")
            .unwrap()
            .to_utc();
        assert_eq!(cache.get("t", "c", early).as_deref(), Some("tok"));
        assert_eq!(
            cache.get("t", "c", late),
            None,
            "inside the 5-minute buffer"
        );
        assert_eq!(cache.get("t", "other", early), None);
    }

    #[test]
    fn put_round_trips_and_preserves_other_entries() {
        let dir = tempfile::tempdir().unwrap();
        let file = dir.path().join("bcli").join("tokens.json");
        let now = DateTime::parse_from_rfc3339("2026-09-30T11:00:00Z")
            .unwrap()
            .to_utc();
        let mut cache = TokenCache::new(file.clone());
        cache.put("t", "a", "tok-a", 3600, now).unwrap();
        cache.put("t", "b", "tok-b", 3600, now).unwrap();

        let text = std::fs::read_to_string(&file).unwrap();
        assert!(
            text.contains("\"expires_at\": \"2026-09-30T12:00:00.000000+00:00\""),
            "{text}"
        );
        let mut fresh = TokenCache::new(file);
        assert_eq!(fresh.get("t", "a", now).as_deref(), Some("tok-a"));
        assert_eq!(fresh.get("t", "b", now).as_deref(), Some("tok-b"));
    }
}
