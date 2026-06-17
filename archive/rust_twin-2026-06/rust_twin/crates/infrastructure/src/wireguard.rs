//! WireGuard tunnel bring-up via `wg-quick`.
//!
//! Linux-only feature. `wg-quick` is a shell script shipped with the
//! `wireguard-tools` package and requires root privileges to actually
//! configure the kernel WireGuard interface. On Windows (or any system
//! where `wg-quick` is not on `PATH`) [`up`] returns
//! [`WgError::BinaryMissing`] so callers can degrade gracefully.
//!
//! The caller is responsible for holding the returned [`WgHandle`] and
//! invoking [`down`] during shutdown. The temporary config file that
//! backs the handle is removed by `down`.

use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use tokio::process::Command;
use tokio::time::timeout;
use tracing::{info, warn};

const CMD_TIMEOUT: Duration = Duration::from_secs(30);
const CONFIG_PREFIX: &str = "wgmesh-";
const CONFIG_SUFFIX: &str = ".conf";

/// Errors that can occur while bringing a WireGuard tunnel up or down.
#[derive(Debug)]
pub enum WgError {
    /// `wg-quick` was not found on `PATH`, or the platform is unsupported
    /// (notably, Windows).
    BinaryMissing,
    /// `wg-quick` exited with a non-zero status caused by insufficient
    /// privileges (no root / CAP_NET_ADMIN).
    PermissionDenied,
    /// `wg-quick` did not complete within [`CMD_TIMEOUT`].
    Timeout,
    /// The child process was spawned but could not be observed
    /// (I/O error talking to it).
    Io(std::io::Error),
    /// `wg-quick` exited with a non-zero status for some other reason.
    CommandFailed { code: Option<i32>, stderr: String },
}

impl std::fmt::Display for WgError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            WgError::BinaryMissing => f.write_str(
                "wg-quick binary not found on PATH (Linux-only feature)",
            ),
            WgError::PermissionDenied => f.write_str(
                "permission denied while invoking wg-quick (root or CAP_NET_ADMIN required)",
            ),
            WgError::Timeout => write!(
                f,
                "wg-quick command did not complete within {:?}",
                CMD_TIMEOUT
            ),
            WgError::Io(e) => write!(f, "io error while running wg-quick: {}", e),
            WgError::CommandFailed { code, stderr } => {
                write!(f, "wg-quick exited with code {:?}: {}", code, stderr)
            }
        }
    }
}

impl std::error::Error for WgError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            WgError::Io(e) => Some(e),
            _ => None,
        }
    }
}

impl From<std::io::Error> for WgError {
    fn from(e: std::io::Error) -> Self {
        WgError::Io(e)
    }
}

/// Handle to a live WireGuard tunnel brought up via [`up`].
///
/// `config_path` points at the on-disk config file used to bring the
/// tunnel up; it is deleted when [`down`] is called. `interface_name`
/// is the name `wg-quick` chose for the interface, which by default
/// mirrors the config file's basename.
#[derive(Debug, Clone)]
pub struct WgHandle {
    pub config_path: PathBuf,
    pub interface_name: String,
}

/// Write `config_text` to a temp file and invoke `wg-quick up <path>`.
///
/// On success, returns a [`WgHandle`] that can be passed to [`down`]
/// to tear the tunnel down and remove the temp file.
pub async fn up(config_text: &str) -> Result<WgHandle, WgError> {
    if cfg!(windows) {
        warn!("wg-quick is a Linux-only feature; refusing to bring up tunnel on Windows");
        return Err(WgError::BinaryMissing);
    }

    let config_path = write_temp_config(config_text)?;
    info!("wrote WireGuard config to {}", config_path.display());

    let path_str = config_path
        .to_str()
        .ok_or_else(|| WgError::CommandFailed {
            code: None,
            stderr: "config path is not valid UTF-8".to_string(),
        })?
        .to_string();

    match run_wg_quick(&["up", &path_str]).await? {
        WgRunResult::Completed(out) => {
            if !out.status.success() {
                let code = out.status.code();
                let stderr = String::from_utf8_lossy(&out.stderr).into_owned();
                let _ = std::fs::remove_file(&config_path);
                if is_permission_error(&stderr) {
                    warn!("wg-quick up: permission denied: {}", stderr);
                    return Err(WgError::PermissionDenied);
                }
                warn!("wg-quick up failed (code={:?}): {}", code, stderr);
                return Err(WgError::CommandFailed { code, stderr });
            }
            let interface_name = interface_name_from_path(&config_path);
            info!(
                "wg-quick up succeeded for interface '{}'",
                interface_name
            );
            Ok(WgHandle {
                config_path,
                interface_name,
            })
        }
        WgRunResult::TimedOut => {
            let _ = std::fs::remove_file(&config_path);
            Err(WgError::Timeout)
        }
    }
}

/// Tear down the tunnel that `handle` describes and remove its temp file.
///
/// Errors from `wg-quick down` are returned to the caller; the temp file
/// is always removed (best-effort) regardless of outcome.
pub async fn down(handle: WgHandle) -> Result<(), WgError> {
    let WgHandle {
        config_path,
        interface_name,
    } = handle;

    let result = run_down(&config_path).await;
    if let Err(ref e) = result {
        warn!(
            "wg-quick down for interface '{}' returned error: {}",
            interface_name, e
        );
    }
    let _ = std::fs::remove_file(&config_path);
    result
}

async fn run_down(config_path: &Path) -> Result<(), WgError> {
    if cfg!(windows) {
        return Err(WgError::BinaryMissing);
    }

    let path_str = config_path
        .to_str()
        .ok_or_else(|| WgError::CommandFailed {
            code: None,
            stderr: "config path is not valid UTF-8".to_string(),
        })?;

    match run_wg_quick(&["down", path_str]).await? {
        WgRunResult::Completed(out) => {
            if out.status.success() {
                Ok(())
            } else {
                let code = out.status.code();
                let stderr = String::from_utf8_lossy(&out.stderr).into_owned();
                if is_permission_error(&stderr) {
                    Err(WgError::PermissionDenied)
                } else {
                    Err(WgError::CommandFailed { code, stderr })
                }
            }
        }
        WgRunResult::TimedOut => Err(WgError::Timeout),
    }
}

enum WgRunResult {
    Completed(std::process::Output),
    TimedOut,
}

async fn run_wg_quick(args: &[&str]) -> Result<WgRunResult, WgError> {
    let mut cmd = Command::new("wg-quick");
    for a in args {
        cmd.arg(a);
    }
    cmd.stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());

    match timeout(CMD_TIMEOUT, cmd.output()).await {
        Ok(Ok(out)) => Ok(WgRunResult::Completed(out)),
        Ok(Err(e)) if e.kind() == std::io::ErrorKind::NotFound => {
            Err(WgError::BinaryMissing)
        }
        Ok(Err(e)) => Err(WgError::Io(e)),
        Err(_) => Ok(WgRunResult::TimedOut),
    }
}

fn is_permission_error(stderr: &str) -> bool {
    let lower = stderr.to_lowercase();
    lower.contains("permission denied")
        || lower.contains("operation not permitted")
        || lower.contains("must be root")
        || lower.contains("need to be root")
        || lower.contains("are not root")
}

fn write_temp_config(config_text: &str) -> Result<PathBuf, WgError> {
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    let path = std::env::temp_dir()
        .join(format!("{}{}{}", CONFIG_PREFIX, nanos, CONFIG_SUFFIX));
    std::fs::write(&path, config_text)?;
    Ok(path)
}

fn interface_name_from_path(path: &Path) -> String {
    path.file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or("wg0")
        .to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp_handle() -> WgHandle {
        WgHandle {
            config_path: PathBuf::from("/nonexistent/path/wgmesh-test.conf"),
            interface_name: "wgmesh-test".to_string(),
        }
    }

    #[tokio::test]
    async fn down_with_nonexistent_handle_does_not_panic() {
        // The specific error variant depends on the environment
        // (BinaryMissing on Windows or on Linux without wg-quick;
        //  CommandFailed / PermissionDenied on Linux with wg-quick).
        // The contract this test enforces: the function must not panic
        // and must surface the failure as an Err.
        let result = down(temp_handle()).await;
        assert!(
            result.is_err(),
            "down on a non-existent handle should return an error"
        );
    }

    #[tokio::test]
    #[ignore = "requires wg-quick installed and root/CAP_NET_ADMIN"]
    async fn up_with_malformed_config_returns_command_failed() {
        // PrivateKey field is invalid base64 → wg-quick will reject it.
        let bad = "[Interface]\nPrivateKey = !!!\nListenPort = 51820\nAddress = 10.0.0.1/24\n";
        match up(bad).await {
            Err(WgError::CommandFailed { .. }) => {}
            Err(other) => panic!("expected WgError::CommandFailed, got {:?}", other),
            Ok(_) => panic!("up with malformed config must not succeed"),
        }
    }

    #[test]
    fn interface_name_derives_from_config_basename() {
        let p = PathBuf::from("/tmp/wgmesh-12345.conf");
        assert_eq!(interface_name_from_path(&p), "wgmesh-12345");
    }

    #[test]
    fn wg_error_display_includes_kind() {
        assert_eq!(
            WgError::BinaryMissing.to_string(),
            "wg-quick binary not found on PATH (Linux-only feature)"
        );
        assert_eq!(
            WgError::PermissionDenied.to_string(),
            "permission denied while invoking wg-quick (root or CAP_NET_ADMIN required)"
        );
        assert!(WgError::Timeout.to_string().contains("did not complete"));
    }
}
