//! Mesh node service.
//!
//! WireGuard peer configuration helpers (a [`WireGuardPeerConfig`] struct,
//! a key-generation function backed by `boringtun`'s x25519 re-export, and
//! a renderer that produces a valid `wg-quick(8)` config file) plus a
//! bridge to `infrastructure::wireguard` that actually brings the tunnel
//! up and tears it down via `wg-quick`.

use base64::Engine;
use sea_orm::DatabaseConnection;
use thiserror::Error;
use uuid::Uuid;

#[derive(Debug, Error)]
pub enum MeshError {
    #[error("database error: {0}")]
    Db(#[from] sea_orm::DbErr),
    #[error("not found: {0}")]
    NotFound(String),
    #[error("key generation error: {0}")]
    KeyGen(String),
}

pub struct MeshService {
    pub db: DatabaseConnection,
}

impl MeshService {
    pub fn new(db: DatabaseConnection) -> Self { Self { db } }

    pub async fn list(&self) -> Result<Vec<crate::entities::mesh_node::Model>, MeshError> {
        use sea_orm::EntityTrait;
        Ok(crate::entities::mesh_node::Entity::find().all(&self.db).await?)
    }

    pub async fn get_by_id(&self, id: Uuid) -> Result<crate::entities::mesh_node::Model, MeshError> {
        use sea_orm::EntityTrait;
        crate::entities::mesh_node::Entity::find_by_id(id).one(&self.db).await?
            .ok_or_else(|| MeshError::NotFound(id.to_string()))
    }
}

// ---------------------------------------------------------------------------
// WireGuard peer configuration
// ---------------------------------------------------------------------------

/// Single peer's WireGuard config, as a plain-data struct. Serialised into
/// the `[Peer]` section of a `wg-quick` config file by [`render_config`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WireGuardPeerConfig {
    /// Peer's Curve25519 public key, base64-encoded.
    pub public_key: String,
    /// Optional pre-shared key, base64-encoded. Use an empty string to
    /// omit it from the rendered config.
    pub preshared_key: String,
    /// Endpoint in `host:port` form (e.g. `"1.2.3.4:51820"`).
    pub endpoint: String,
    /// AllowedIPs for this peer (e.g. `["10.0.0.2/32"]`).
    pub allowed_ips: Vec<String>,
    /// Persistent keepalive interval in seconds. `0` disables keepalives
    /// and the field is omitted from the rendered config.
    pub persistent_keepalive: u16,
}

/// Local interface section of a `wg-quick` config.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WgInterface {
    /// Private key of the local interface, base64-encoded.
    pub private_key: String,
    /// Local listen port.
    pub listen_port: u16,
    /// Address (CIDR) for the local interface, e.g. `"10.0.0.1/24"`.
    pub address: String,
}

/// Generate a WireGuard key pair using boringtun's x25519 re-export.
///
/// Returns `(private_key_b64, public_key_b64)`. Uses `OsRng` for entropy;
/// does not roll its own X25519.
pub fn generate_keypair() -> (String, String) {
    use boringtun::x25519::{PublicKey, StaticSecret};
    use rand::rngs::OsRng;

    let secret = StaticSecret::random_from_rng(OsRng);
    let public = PublicKey::from(&secret);
    let b64 = base64::engine::general_purpose::STANDARD;
    (b64.encode(secret.to_bytes()), b64.encode(public.to_bytes()))
}

/// Render a `wg-quick(8)` configuration file.
///
/// The output uses `\n` line endings, has an `[Interface]` section followed
/// by zero or more `[Peer]` sections, and is accepted by both `wg-quick`
/// and `wg setconf` (modulo `Address =` which is a `wg-quick`-only
/// extension).
pub fn render_config(interface: &WgInterface, peers: &[WireGuardPeerConfig]) -> String {
    let mut out = String::new();
    out.push_str("[Interface]\n");
    out.push_str(&format!("PrivateKey = {}\n", interface.private_key));
    out.push_str(&format!("ListenPort = {}\n", interface.listen_port));
    out.push_str(&format!("Address = {}\n", interface.address));

    for peer in peers {
        out.push_str("\n[Peer]\n");
        out.push_str(&format!("PublicKey = {}\n", peer.public_key));
        if !peer.preshared_key.is_empty() {
            out.push_str(&format!("PresharedKey = {}\n", peer.preshared_key));
        }
        if !peer.endpoint.is_empty() {
            out.push_str(&format!("Endpoint = {}\n", peer.endpoint));
        }
        if !peer.allowed_ips.is_empty() {
            out.push_str(&format!("AllowedIPs = {}\n", peer.allowed_ips.join(", ")));
        }
        if peer.persistent_keepalive > 0 {
            out.push_str(&format!("PersistentKeepalive = {}\n", peer.persistent_keepalive));
        }
    }

    out
}

// ---------------------------------------------------------------------------
// Bridge to infrastructure::wireguard
// ---------------------------------------------------------------------------

/// Re-export of the tunnel handle produced by [`bring_up_mesh`].
pub use infrastructure::wireguard::WgHandle;
/// Error type returned by [`bring_up_mesh`] and [`tear_down_mesh`].
pub use infrastructure::wireguard::WgError;

/// Default listen port used when bringing a mesh interface up.
pub const DEFAULT_LISTEN_PORT: u16 = 51820;
/// Default address (CIDR) used for the local mesh interface.
pub const DEFAULT_ADDRESS: &str = "10.0.0.1/24";

/// Render the mesh's `wg-quick` config and bring the tunnel up by
/// shelling out to `wg-quick up <tempfile>`. Returns a [`WgHandle`]
/// that must be passed back to [`tear_down_mesh`] during shutdown.
///
/// On Windows or any system where `wg-quick` is not on `PATH`, this
/// returns [`WgError::BinaryMissing`]. When the process is not run
/// as root, it returns [`WgError::PermissionDenied`]. See
/// [`infrastructure::wireguard::WgError`] for the full variant list.
pub async fn bring_up_mesh(
    peers: &[WireGuardPeerConfig],
    my_private_key: &str,
) -> Result<WgHandle, WgError> {
    let interface = WgInterface {
        private_key: my_private_key.to_string(),
        listen_port: DEFAULT_LISTEN_PORT,
        address: DEFAULT_ADDRESS.to_string(),
    };
    let config_text = render_config(&interface, peers);
    infrastructure::wireguard::up(&config_text).await
}

/// Tear down the tunnel described by `handle` (i.e. shell out to
/// `wg-quick down <tempfile>`) and remove the temp file.
pub async fn tear_down_mesh(handle: WgHandle) -> Result<(), WgError> {
    infrastructure::wireguard::down(handle).await
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_error_display() {
        assert_eq!(MeshError::NotFound("x".into()).to_string(), "not found: x");
    }

    #[test]
    fn render_config_emits_interface_and_peers() {
        let iface = WgInterface {
            private_key: "kIICJxR9O5K2Jg1l3LJ7zZ2qV4u0Y6E2s9CkE0C1L0g=".to_string(),
            listen_port: 51820,
            address: "10.0.0.1/24".to_string(),
        };
        let peers = vec![
            WireGuardPeerConfig {
                public_key: "hSDwCY4MrH8T3g0X3K7r1Y8Kj5j2Zc1FQ1l+9c4s6Bw=".to_string(),
                preshared_key: "base64preshared==".to_string(),
                endpoint: "1.2.3.4:51820".to_string(),
                allowed_ips: vec!["10.0.0.2/32".to_string()],
                persistent_keepalive: 25,
            },
            WireGuardPeerConfig {
                public_key: "hSDwCY4MrH8T3g0X3K7r1Y8Kj5j2Zc1FQ1l+9c4s6Bx=".to_string(),
                preshared_key: String::new(),
                endpoint: String::new(),
                allowed_ips: vec!["10.0.0.3/32".to_string(), "fd00::3/128".to_string()],
                persistent_keepalive: 0,
            },
        ];

        let cfg = render_config(&iface, &peers);

        assert!(cfg.contains("[Interface]"));
        assert!(cfg.contains("PrivateKey = kIICJxR9O5K2Jg1l3LJ7zZ2qV4u0Y6E2s9CkE0C1L0g="));
        assert!(cfg.contains("ListenPort = 51820"));
        assert!(cfg.contains("Address = 10.0.0.1/24"));

        assert!(cfg.contains("[Peer]"));
        assert!(cfg.contains("PublicKey = hSDwCY4MrH8T3g0X3K7r1Y8Kj5j2Zc1FQ1l+9c4s6Bw="));
        assert!(cfg.contains("PresharedKey = base64preshared=="));
        assert!(cfg.contains("Endpoint = 1.2.3.4:51820"));
        assert!(cfg.contains("AllowedIPs = 10.0.0.2/32"));
        assert!(cfg.contains("PersistentKeepalive = 25"));

        assert!(cfg.contains("PublicKey = hSDwCY4MrH8T3g0X3K7r1Y8Kj5j2Zc1FQ1l+9c4s6Bx="));
        assert!(cfg.contains("AllowedIPs = 10.0.0.3/32, fd00::3/128"));
        assert!(!cfg.contains("PersistentKeepalive = 0"));
    }

    #[test]
    fn render_config_with_no_peers_is_valid() {
        let iface = WgInterface {
            private_key: "AAAA".to_string(),
            listen_port: 51820,
            address: "10.0.0.1/24".to_string(),
        };
        let cfg = render_config(&iface, &[]);
        assert!(cfg.starts_with("[Interface]"));
        assert!(!cfg.contains("[Peer]"));
    }

    #[test]
    fn render_config_private_key_is_valid_base64() {
        // A real x25519 key from boringtun: 32 raw bytes encodes to
        // exactly 44 base64 chars (with `=` padding).
        let iface = WgInterface {
            private_key: "kIICJxR9O5K2Jg1l3LJ7zZ2qV4u0Y6E2s9CkE0C1L0g=".to_string(),
            listen_port: 51820,
            address: "10.0.0.1/24".to_string(),
        };
        let cfg = render_config(&iface, &[]);
        let line = cfg
            .lines()
            .find(|l| l.starts_with("PrivateKey = "))
            .expect("rendered config contains a PrivateKey line");
        let value = line.trim_start_matches("PrivateKey = ").trim();
        assert_eq!(value.len(), 44, "32-byte key encodes to 44 base64 chars");
        assert!(
            value.chars().all(|c| {
                c.is_ascii_alphanumeric() || c == '+' || c == '/' || c == '='
            }),
            "PrivateKey must be valid base64 characters"
        );
        let decoded = base64::engine::general_purpose::STANDARD
            .decode(value)
            .expect("PrivateKey must decode as standard base64");
        assert_eq!(decoded.len(), 32, "decoded WireGuard key must be 32 bytes");
    }

    #[test]
    fn generate_keypair_returns_base64_x25519_keys() {
        let (priv_b64, pub_b64) = generate_keypair();
        let b64 = base64::engine::general_purpose::STANDARD;
        let priv_bytes = b64.decode(&priv_b64).expect("priv must be base64");
        let pub_bytes = b64.decode(&pub_b64).expect("pub must be base64");
        assert_eq!(priv_bytes.len(), 32, "private key must be 32 bytes");
        assert_eq!(pub_bytes.len(), 32, "public key must be 32 bytes");
        let (priv2, pub2) = generate_keypair();
        assert_ne!(priv_b64, priv2);
        assert_ne!(pub_b64, pub2);
    }
}
