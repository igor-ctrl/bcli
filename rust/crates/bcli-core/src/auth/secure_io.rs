//! Private (0600) atomic writes for files that hold credentials.
//!
//! Same contract as `bcli.auth._secure_io`: parent dir 0700, payload written
//! to `<name>.tmp` created with 0600, then renamed into place. On Windows
//! only the atomic rename applies; the threat model there is the user account.

use std::fs;
use std::io::{self, Write};
use std::path::Path;

#[cfg(unix)]
const DIR_MODE: u32 = 0o700;
#[cfg(unix)]
const FILE_MODE: u32 = 0o600;
#[cfg(unix)]
const INSECURE_MASK: u32 = 0o077;

pub fn write_secret_file(path: &Path, content: &str) -> io::Result<Vec<String>> {
    let mut warnings = Vec::new();
    if let Some(parent) = path.parent() {
        ensure_private_dir(parent, &mut warnings)?;
    }
    let mut tmp_name = path.file_name().unwrap_or_default().to_os_string();
    tmp_name.push(".tmp");
    let tmp = path.with_file_name(tmp_name);
    let _ = fs::remove_file(&tmp);

    let mut options = fs::OpenOptions::new();
    options.write(true).create(true).truncate(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(FILE_MODE);
    }
    let result = options.open(&tmp).and_then(|mut f| {
        f.write_all(content.as_bytes())?;
        f.sync_all()
    });
    if let Err(e) = result {
        let _ = fs::remove_file(&tmp);
        return Err(e);
    }
    fs::rename(&tmp, path)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(path, fs::Permissions::from_mode(FILE_MODE))?;
    }
    Ok(warnings)
}

/// Tighten a credential file left group/other-readable by an older bcli.
pub fn tighten_if_insecure(path: &Path) -> Option<String> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mode = fs::metadata(path).ok()?.permissions().mode() & 0o777;
        if mode & INSECURE_MASK != 0 {
            let _ = fs::set_permissions(path, fs::Permissions::from_mode(FILE_MODE));
            return Some(format!(
                "{} had loose permissions ({mode:#o}); tightening to {FILE_MODE:#o}. Other local \
                 users may have been able to read cached credentials before this bcli upgrade.",
                path.display()
            ));
        }
        None
    }
    #[cfg(not(unix))]
    {
        let _ = path;
        None
    }
}

fn ensure_private_dir(dir: &Path, warnings: &mut Vec<String>) -> io::Result<()> {
    fs::create_dir_all(dir)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mode = fs::metadata(dir)?.permissions().mode() & 0o777;
        if mode & INSECURE_MASK != 0 {
            warnings.push(format!(
                "{} had loose permissions ({mode:#o}); tightening to {DIR_MODE:#o}.",
                dir.display()
            ));
            fs::set_permissions(dir, fs::Permissions::from_mode(DIR_MODE))?;
        }
    }
    #[cfg(not(unix))]
    let _ = warnings;
    Ok(())
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;
    use std::os::unix::fs::PermissionsExt;

    #[test]
    fn writes_0600_inside_0700_dir() {
        let root = tempfile::tempdir().unwrap();
        let dir = root.path().join("bcli");
        fs::create_dir(&dir).unwrap();
        fs::set_permissions(&dir, fs::Permissions::from_mode(0o755)).unwrap();
        let file = dir.join("tokens.json");
        let warnings = write_secret_file(&file, "{}").unwrap();
        assert_eq!(warnings.len(), 1);
        assert_eq!(
            fs::metadata(&dir).unwrap().permissions().mode() & 0o777,
            0o700
        );
        assert_eq!(
            fs::metadata(&file).unwrap().permissions().mode() & 0o777,
            0o600
        );
        assert_eq!(fs::read_to_string(&file).unwrap(), "{}");
        assert!(!dir.join("tokens.json.tmp").exists());
    }
}
