//! HMAC-SHA256 signing for webhook payloads.

use hmac::{Hmac, Mac};
use sha2::Sha256;
use hex;

type HmacSha256 = Hmac<Sha256>;

pub fn sign(secret: &str, payload: &[u8]) -> String {
    let mut mac = HmacSha256::new_from_slice(secret.as_bytes())
        .expect("HMAC accepts any key length");
    mac.update(payload);
    let result = mac.finalize();
    let bytes = result.into_bytes();
    format!("sha256={}", hex::encode(bytes))
}

pub fn verify(secret: &str, payload: &[u8], signature: &str) -> bool {
    let expected = sign(secret, payload);
    // Constant-time compare
    if expected.len() != signature.len() { return false; }
    let mut diff = 0u8;
    for (a, b) in expected.bytes().zip(signature.bytes()) {
        diff |= a ^ b;
    }
    diff == 0
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_sign_produces_consistent_output() {
        let s = sign("secret", b"hello");
        assert!(s.starts_with("sha256="));
        assert_eq!(s.len(), 7 + 64);  // "sha256=" + 64 hex chars
    }

    #[test]
    fn test_verify_roundtrip() {
        let secret = "topsecret";
        let payload = b"the quick brown fox";
        let sig = sign(secret, payload);
        assert!(verify(secret, payload, &sig));
    }

    #[test]
    fn test_verify_wrong_secret() {
        let payload = b"the quick brown fox";
        let sig = sign("secret1", payload);
        assert!(!verify("secret2", payload, &sig));
    }

    #[test]
    fn test_verify_tampered() {
        let secret = "secret";
        let payload = b"original";
        let sig = sign(secret, payload);
        assert!(!verify(secret, b"tampered", &sig));
    }
}
