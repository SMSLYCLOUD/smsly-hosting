use anyhow::{anyhow, Context, Result};
use argon2::{
    password_hash::{
        rand_core::{OsRng, RngCore},
        PasswordHash, PasswordHasher, PasswordVerifier, SaltString,
    },
    Argon2,
};
use base64::{engine::general_purpose, Engine as _};
use pbkdf2::pbkdf2_hmac;
use jsonwebtoken::{decode, encode, DecodingKey, EncodingKey, Header, Validation};
use serde::{Deserialize, Serialize};
use sha1::Sha1;
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::sync::{Arc, Mutex};

#[derive(Debug, Serialize, Deserialize)]
pub struct Claims {
    pub sub: i32,     // The user ID
    pub exp: usize,   // Expiration timestamp
    pub iat: usize,   // Issued at timestamp
}

/// Hash algorithm as encoded in the stored password string.
///
/// The `rust_twin` mirrors the Django backend's `PASSWORD_HASHERS` list
/// (`Argon2PasswordHasher`, `PBKDF2PasswordHasher`, `BCryptSHA256PasswordHasher`,
/// `PBKDF2SHA1PasswordHasher`). The first segment of a stored hash (before the
/// first `$`) identifies which algorithm produced it.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum HashAlgo {
    Argon2,
    BcryptSha256,   // Django's bcrypt_sha256 wrapper (bcrypt over SHA-256 of password)
    Pbkdf2Sha256,   // Django's pbkdf2_sha256
    Pbkdf2Sha1,     // Legacy Django pbkdf2_sha1 (kept for migration period)
}

impl HashAlgo {
    /// Parse the algorithm prefix from a Django-style hash string.
    /// Returns None if the prefix is unrecognised.
    pub fn detect(stored: &str) -> Option<Self> {
        if stored.starts_with("argon2$") {
            Some(HashAlgo::Argon2)
        } else if stored.starts_with("bcrypt_sha256$") {
            Some(HashAlgo::BcryptSha256)
        } else if stored.starts_with("pbkdf2_sha256$") {
            Some(HashAlgo::Pbkdf2Sha256)
        } else if stored.starts_with("pbkdf2_sha1$") {
            Some(HashAlgo::Pbkdf2Sha1)
        } else {
            None
        }
    }
}

pub struct AuthUtils;

impl AuthUtils {
    /// Hash a password using Argon2 (the default for new accounts in the
    /// `rust_twin`). The returned string is prefixed with `argon2$` so the
    /// stored hash is self-describing.
    pub fn hash_password(password: &str) -> Result<String> {
        let salt = SaltString::generate(&mut OsRng);
        let argon2 = Argon2::default();
        let hash = argon2
            .hash_password(password.as_bytes(), &salt)
            .map_err(|e| anyhow!("Failed to hash password: {}", e))?
            .to_string();
        Ok(format!("argon2${}", hash))
    }

    /// Verify a raw password against a stored hash, supporting all Django
    /// hash formats. The algorithm is auto-detected from the stored hash.
    pub fn verify_password(password: &str, stored_hash: &str) -> Result<bool> {
        let algo = HashAlgo::detect(stored_hash)
            .ok_or_else(|| anyhow!("Unrecognised password hash format"))?;
        // Strip the leading "<algo>$" prefix; the remainder is the raw
        // algorithm-specific hash (e.g. Argon2 PHC string, bcrypt PHC string,
        // or `iter$salt$hash` for PBKDF2).
        let raw = stored_hash
            .split_once('$')
            .map(|x| x.1)
            .unwrap_or(stored_hash);

        match algo {
            HashAlgo::Argon2 => {
                let parsed = PasswordHash::new(raw)
                    .map_err(|e| anyhow!("Invalid Argon2 hash: {}", e))?;
                Ok(Argon2::default()
                    .verify_password(password.as_bytes(), &parsed)
                    .is_ok())
            }
            HashAlgo::BcryptSha256 => verify_bcrypt_sha256(password, raw),
            HashAlgo::Pbkdf2Sha256 => verify_pbkdf2_sha256(password, raw),
            HashAlgo::Pbkdf2Sha1 => verify_pbkdf2_sha1(password, raw),
        }
    }

    /// Check whether a stored hash should be upgraded to Argon2 on next login.
    /// PBKDF2 variants are considered legacy; bcrypt_sha256 is acceptable
    /// but Argon2 is preferred (we keep it upgrade-eligible when the cost
    /// is low, but for simplicity we treat it as "up to date" here).
    pub fn needs_rehash(stored_hash: &str) -> bool {
        match HashAlgo::detect(stored_hash) {
            Some(HashAlgo::Argon2) | Some(HashAlgo::BcryptSha256) => false,
            Some(HashAlgo::Pbkdf2Sha256) | Some(HashAlgo::Pbkdf2Sha1) => true,
            None => true, // unknown format -> force rehash
        }
    }

    /// Re-hash a password using Argon2 after a successful verify.
    pub fn rehash_to_argon2(password: &str) -> Result<String> {
        Self::hash_password(password)
    }

    /// Generates a JWT token for the given user ID, valid for 24 hours.
    pub fn create_jwt(user_id: i32, secret_key: &str) -> Result<String> {
        let now = chrono::Utc::now().timestamp() as usize;
        // Expire in 24 hours
        let exp = now + (24 * 60 * 60);

        let claims = Claims {
            sub: user_id,
            exp,
            iat: now,
        };

        let token = encode(
            &Header::default(),
            &claims,
            &EncodingKey::from_secret(secret_key.as_ref()),
        )
        .context("Failed to encode JWT token")?;

        Ok(token)
    }

    /// Validates a JWT token string and returns the contained claims (e.g., user_id).
    pub fn decode_jwt(token: &str, secret_key: &str) -> Result<Claims> {
        let mut validation = Validation::default();
        validation.leeway = 60; // 60 seconds of clock skew leeway

        let token_data = decode::<Claims>(
            token,
            &DecodingKey::from_secret(secret_key.as_ref()),
            &validation,
        )
        .context("Failed to decode or validate JWT token")?;

        Ok(token_data.claims)
    }
}

/// Generate a DRF-style authentication token: 20 random bytes from the OS RNG,
/// hex-encoded to a 40-character lowercase string.
///
/// This matches the wire format of Django REST Framework's
/// `rest_framework.authtoken.models.Token.key` field, and is the format the
/// Django backend issues from `obtain_auth_token`. The middleware in
/// `cn_api::middleware::auth_user` accepts it in an `Authorization: Token <hex>`
/// header or in the `__Host-smsly_token` HttpOnly cookie.
pub fn generate_drf_token() -> String {
    let mut bytes = [0u8; 20];
    OsRng.fill_bytes(&mut bytes);
    hex::encode(bytes)
}

/// A login response token pair: a JWT (HS256, 24h, kept for backward
/// compatibility with the existing client SDKs) and a DRF-style 40-char hex
/// token that mirrors the Django backend's `obtain_auth_token` payload.
///
/// `drf_token` is what the browser stores in the `__Host-smsly_token`
/// HttpOnly cookie and what the middleware looks up via [`DrfTokenStore`].
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SignedToken {
    pub jwt: String,
    pub drf_token: String,
}

impl SignedToken {
    /// Issue a fresh (jwt, drf_token) pair for the given user.
    pub fn issue(user_id: i32, secret_key: &str) -> Result<Self> {
        Ok(Self {
            jwt: AuthUtils::create_jwt(user_id, secret_key)?,
            drf_token: generate_drf_token(),
        })
    }
}

/// In-memory store mapping DRF tokens to user IDs.
///
/// **Scope note:** This is intentionally a process-local cache for the polarity
/// bridge (Gap 2: auth token bridge). The persistent equivalent on the Django
/// side is the `authtoken_token` table (`key`, `user_id`, `created`). Adding
/// a `drf_token` SeaORM entity is owned by Agent 1 (entity work) and is out of
/// scope for this change — see the polarity report.
///
/// Use [`DrfTokenStore::register`] at login / register / refresh time and
/// [`DrfTokenStore::resolve`] from the auth middleware. The store is cheaply
/// cloneable (`Arc` inside) and is meant to live on `AppState`.
#[derive(Debug, Default, Clone)]
pub struct DrfTokenStore {
    inner: Arc<Mutex<HashMap<String, i32>>>,
}

impl DrfTokenStore {
    pub fn new() -> Self {
        Self {
            inner: Arc::new(Mutex::new(HashMap::new())),
        }
    }

    /// Bind a DRF token to a user id. Overwrites any prior binding for the
    /// same token string.
    pub fn register(&self, user_id: i32, token: &str) {
        if let Ok(mut map) = self.inner.lock() {
            map.insert(token.to_string(), user_id);
        }
    }

    /// Look up the user id associated with a DRF token, if any.
    pub fn resolve(&self, token: &str) -> Option<i32> {
        self.inner
            .lock()
            .ok()
            .and_then(|m| m.get(token).copied())
    }

    /// Remove a DRF token binding (used on logout). Missing keys are a no-op.
    pub fn revoke(&self, token: &str) {
        if let Ok(mut map) = self.inner.lock() {
            map.remove(token);
        }
    }
}

/// Verify a Django `bcrypt_sha256$` hash.
///
/// Django's `BCryptSHA256PasswordHasher`:
///   1. Computes `SHA-256(password)`,
///   2. Base64-encodes the 32-byte digest (standard alphabet, padded), and
///   3. Runs `bcrypt` over that base64 string (44 ASCII chars, well under
///      bcrypt's 72-byte input limit, so bcrypt's own truncation never bites).
///
/// The portion after `bcrypt_sha256$` is a normal bcrypt PHC string
/// (`$2b$<cost>$<22-char salt><31-char hash>`).
fn verify_bcrypt_sha256(password: &str, raw_hash: &str) -> Result<bool> {
    let mut sha = Sha256::new();
    sha.update(password.as_bytes());
    let digest = sha.finalize();
    // Base64-encode the raw SHA-256 digest (with `=` padding) exactly like
    // Django's `b64encode` does.
    let bcrypt_input = general_purpose::STANDARD.encode(digest);
    Ok(bcrypt::verify(&bcrypt_input, raw_hash).unwrap_or(false))
}

/// Verify a Django PBKDF2-SHA256 hash.
///
/// The `raw_hash` portion (after the algorithm prefix) has the form
/// `iterations$salt$base64(hash)`. The salt is 12 ASCII characters from
/// the set `string.ascii_letters + string.digits + "./"` (NOT base64), and
/// the hash is the standard base64-encoded digest (with `=` padding).
fn verify_pbkdf2_sha256(password: &str, raw_hash: &str) -> Result<bool> {
    let parts: Vec<&str> = raw_hash.split('$').collect();
    if parts.len() != 3 {
        return Err(anyhow!(
            "Malformed pbkdf2_sha256 hash: expected 3 parts, got {}",
            parts.len()
        ));
    }
    let iterations: u32 = parts[0].parse().context("pbkdf2 iterations")?;
    let salt = parts[1].as_bytes();
    let expected = general_purpose::STANDARD
        .decode(parts[2])
        .map_err(|e| anyhow!("pbkdf2 hash decode: {}", e))?;
    let mut actual = vec![0u8; expected.len()];
    pbkdf2_hmac::<Sha256>(password.as_bytes(), salt, iterations, &mut actual);
    Ok(actual.as_slice() == expected.as_slice())
}

/// Verify a legacy Django PBKDF2-SHA1 hash. Same wire format as
/// `pbkdf2_sha256` but the digest is 20 bytes (SHA-1).
fn verify_pbkdf2_sha1(password: &str, raw_hash: &str) -> Result<bool> {
    let parts: Vec<&str> = raw_hash.split('$').collect();
    if parts.len() != 3 {
        return Err(anyhow!(
            "Malformed pbkdf2_sha1 hash: expected 3 parts, got {}",
            parts.len()
        ));
    }
    let iterations: u32 = parts[0].parse().context("pbkdf2 iterations")?;
    let salt = parts[1].as_bytes();
    let expected = general_purpose::STANDARD
        .decode(parts[2])
        .map_err(|e| anyhow!("pbkdf2 hash decode: {}", e))?;
    let mut actual = vec![0u8; expected.len()];
    pbkdf2_hmac::<Sha1>(password.as_bytes(), salt, iterations, &mut actual);
    Ok(actual.as_slice() == expected.as_slice())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_hash_and_verify_argon2() {
        let hash = AuthUtils::hash_password("hunter2").unwrap();
        assert!(hash.starts_with("argon2$"), "expected argon2 prefix, got: {}", hash);
        assert!(AuthUtils::verify_password("hunter2", &hash).unwrap());
        assert!(!AuthUtils::verify_password("wrong", &hash).unwrap());
    }

    #[test]
    fn test_detect_algorithms() {
        assert_eq!(
            HashAlgo::detect("argon2$argon2id$v=19$m=102400,t=2,p=8$abc$def"),
            Some(HashAlgo::Argon2)
        );
        assert_eq!(
            HashAlgo::detect("bcrypt_sha256$2b$12$abcdefghijklmnopqrstuvabcdefghijklmnopqrstuvwxyz12"),
            Some(HashAlgo::BcryptSha256)
        );
        assert_eq!(
            HashAlgo::detect("pbkdf2_sha256$600000$abcdef012345$BBBB"),
            Some(HashAlgo::Pbkdf2Sha256)
        );
        assert_eq!(
            HashAlgo::detect("pbkdf2_sha1$1000$saltsaltsalt$BBBB"),
            Some(HashAlgo::Pbkdf2Sha1)
        );
        assert_eq!(HashAlgo::detect("garbage"), None);
        assert_eq!(HashAlgo::detect(""), None);
    }

    #[test]
    fn test_needs_rehash() {
        assert!(!AuthUtils::needs_rehash("argon2$argon2id$v=19$..."));
        assert!(!AuthUtils::needs_rehash(
            "bcrypt_sha256$2b$12$abcdefghijklmnopqrstuvabcdefghijklmnopqrstuvwxyz12"
        ));
        assert!(AuthUtils::needs_rehash("pbkdf2_sha256$600000$..."));
        assert!(AuthUtils::needs_rehash("pbkdf2_sha1$1000$..."));
        assert!(AuthUtils::needs_rehash("garbage"));
    }

    #[test]
    fn test_verify_pbkdf2_sha256_wrong_password() {
        // Build a PBKDF2-SHA256 hash in the exact Django format:
        //   "pbkdf2_sha256$<iterations>$<ascii salt>$<base64 hash>"
        // Django's salt is 12 ASCII characters from the alphabet
        // [A-Za-z0-9./] and the hash is the standard base64-encoded
        // binary digest (with `=` padding).
        let password = "correct horse battery staple";
        let salt = "abcdef012345"; // 12-char ASCII "salt"
        let iterations: u32 = 1000;
        let mut buf = [0u8; 32];
        pbkdf2_hmac::<Sha256>(password.as_bytes(), salt.as_bytes(), iterations, &mut buf);
        let expected = general_purpose::STANDARD.encode(buf);

        let stored = format!("pbkdf2_sha256${}${}${}", iterations, salt, expected);

        assert!(AuthUtils::verify_password(password, &stored).unwrap());
        assert!(!AuthUtils::verify_password("wrong", &stored).unwrap());
    }

    #[test]
    fn test_verify_pbkdf2_sha1_wrong_password() {
        let password = "legacy user";
        let salt = "saltsaltsalt";
        let iterations: u32 = 1000;
        let mut buf = [0u8; 20];
        pbkdf2_hmac::<Sha1>(password.as_bytes(), salt.as_bytes(), iterations, &mut buf);
        let expected = general_purpose::STANDARD.encode(buf);

        let stored = format!("pbkdf2_sha1${}${}${}", iterations, salt, expected);

        assert!(AuthUtils::verify_password(password, &stored).unwrap());
        assert!(!AuthUtils::verify_password("nope", &stored).unwrap());
    }

    #[test]
    fn test_rehash_to_argon2() {
        let new_hash = AuthUtils::rehash_to_argon2("hunter2").unwrap();
        assert!(new_hash.starts_with("argon2$"));
        assert!(AuthUtils::verify_password("hunter2", &new_hash).unwrap());
    }

    #[test]
    fn test_verify_unrecognised_format_errors() {
        let res = AuthUtils::verify_password("anything", "not-a-real-hash");
        assert!(res.is_err());
    }

    #[test]
    fn test_generate_drf_token_format() {
        let t = generate_drf_token();
        assert_eq!(t.len(), 40, "DRF token must be 40 chars, got {} ({})", t.len(), t);
        assert!(
            t.chars().all(|c| c.is_ascii_hexdigit() && !c.is_ascii_uppercase()),
            "DRF token must be lowercase hex, got: {}",
            t
        );
    }

    #[test]
    fn test_generate_drf_token_unique() {
        // Two tokens from the same RNG seed context should never collide
        // across 256 calls (20 bytes of entropy each).
        let mut seen = std::collections::HashSet::new();
        for _ in 0..256 {
            let t = generate_drf_token();
            assert!(seen.insert(t.clone()), "DRF token collision: {}", t);
        }
    }

    #[test]
    fn test_signed_token_issue() {
        let pair = SignedToken::issue(7, "secret").unwrap();
        assert_eq!(pair.drf_token.len(), 40);
        // JWT round-trip
        let claims = AuthUtils::decode_jwt(&pair.jwt, "secret").unwrap();
        assert_eq!(claims.sub, 7);
    }

    #[test]
    fn test_drf_token_store_roundtrip() {
        let store = DrfTokenStore::new();
        let token = generate_drf_token();
        store.register(42, &token);
        assert_eq!(store.resolve(&token), Some(42));
        assert_eq!(store.resolve("not-a-real-token"), None);
        store.revoke(&token);
        assert_eq!(store.resolve(&token), None);
    }
}
